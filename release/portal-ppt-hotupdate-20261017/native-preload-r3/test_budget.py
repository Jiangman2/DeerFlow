import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock
import types
import stat

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('allocation_under_test',HERE/'budget_runtime.py')
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)

def small_plan():
    base=dict(chain_id='base',parent='',blob_digest='baseblob',diff_id='base',compressed_bytes=30)
    added=dict(chain_id='added',parent='base',blob_digest='newblob',diff_id='added',compressed_bytes=10)
    return dict(images=[dict(layers=[base,added]),dict(layers=[base,added])],chains={'base':base,'added':added},
        blobs={'baseblob':30,'newblob':10},maximum_outer_blob_bytes=30,
        allocation=dict(physical_by_chain={'added':100},required_cached_chains=1,maximum_metadata_import_bytes=5))

class BudgetTests(unittest.TestCase):
    def test_two_phase_deduplicates_and_does_not_overlap_full_scratch(self):
        p=small_plan();s={'base':dict(parent='',kind='Committed')}
        r=A.budget(p,{'baseblob'},s,'BEFORE_PHASE_A')
        self.assertEqual(r['missing_content_bytes'],10)
        self.assertEqual(r['remaining_physical_allowance_bytes'],100)
        self.assertEqual(r['incremental_peak_allowance_bytes'],115+A.METADATA)

    def test_phase_b_requires_content_closure(self):
        p=small_plan();s={'base':dict(parent='',kind='Committed')}
        with self.assertRaisesRegex(ValueError,'content_closure_lost'):A.budget(p,{'baseblob'},s,'BEFORE_PHASE_B')
        r=A.budget(p,set(p['blobs']),s,'BEFORE_PHASE_B')
        self.assertEqual(r['incremental_peak_allowance_bytes'],105+A.METADATA)

    def test_base_cache_loss_and_noncommitted_fail(self):
        for snapshots in ({},{'base':dict(parent='',kind='Active')}):
            with self.assertRaisesRegex(ValueError,'cached_base_lost'):
                A.budget(small_plan(),{'baseblob'},snapshots,'BEFORE_PHASE_A')

    def test_completed_snapshot_is_credited_only_with_complete_parent_chain(self):
        p=small_plan();s={'base':dict(parent='',kind='Committed'),'added':dict(parent='base',kind='Committed')}
        self.assertEqual(A.budget(p,set(p['blobs']),s,'BEFORE_PHASE_B')['remaining_physical_allowance_bytes'],0)
        s['added']['parent']='wrong'
        self.assertEqual(A.budget(p,set(p['blobs']),s,'BEFORE_PHASE_B')['remaining_physical_allowance_bytes'],100)

    def test_exact_snapshot_projection(self):
        p=small_plan()
        self.assertEqual(A.parse_snapshots(b'KEY PARENT KIND\nbase Committed\nadded base Committed\n',p['chains']),
                         {'base':dict(parent='',kind='Committed'),'added':dict(parent='base',kind='Committed')})
        with self.assertRaisesRegex(ValueError,'snapshot_parent'):
            A.parse_snapshots(b'KEY PARENT KIND\nadded wrong Committed\n',p['chains'])

    def test_live_budget_exact_threshold_and_failure_evidence(self):
        p=small_plan();image=dict(path='/image',device=1,capacity_bytes=100*A.GIB,available_bytes=0,available_inodes=100001)
        opt=dict(path='/opt',device=2,capacity_bytes=200*A.GIB,available_bytes=100*A.GIB,available_inodes=100001)
        p['allocation']['filesystems']=dict(root=dict(image,path='/var/lib'),opt=opt,image_filesystems=[image])
        required=A.reserve(image)+A.HEADROOM+115+A.METADATA
        def row(path,expected,fd=None):return dict(expected,available_bytes=required if path!='/opt' else 100*A.GIB)
        def capture(argv,phase,**kwargs):
            if phase=='R3.CONTENT':return b'baseblob\n'
            if phase=='R3.SNAPSHOTS':return b'KEY PARENT KIND\nbase Committed\n'
            if phase=='R3.FILESYSTEM':return b'xfs\n'
            return b'{}'
        B=types.SimpleNamespace(CRI=['crictl'],CTR=['ctr'],capture=capture,parse=json.loads,image_fs_paths=lambda v:['/image'])
        with mock.patch.object(A,'stat_row',side_effect=row):
            result=A.check(B,dict(images=[dict(archive_bytes=100)]),p,'BEFORE_PHASE_A')
            self.assertTrue(result['storage_budget_pass'])
        def low(path,expected,fd=None):
            value=row(path,expected,fd)
            if path=='/image':value['available_bytes']-=1
            return value
        with mock.patch.object(A,'stat_row',side_effect=low):
            with self.assertRaisesRegex(ValueError,'storage_budget_insufficient') as caught:
                A.check(B,dict(images=[dict(archive_bytes=100)]),p,'BEFORE_PHASE_A')
            self.assertFalse(caught.exception.observation['storage_budget_pass'])

    def test_unreviewed_block_size_is_rejected(self):
        s=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_dev=1)
        v=types.SimpleNamespace(f_blocks=10,f_frsize=4096,f_bsize=8192,f_flag=0,f_bavail=9,f_favail=100000)
        with mock.patch.object(A.os,'stat',return_value=s),mock.patch.object(A.os,'statvfs',return_value=v,create=True):
            with self.assertRaisesRegex(ValueError,'unsupported_block_geometry'):
                A.stat_row('/image',dict(device=1,capacity_bytes=40960))

    def test_real_catalog_and_report_bind_all_known_missing_chains(self):
        # Host's remote-only tail is synthetic here. This exercises plan plumbing,
        # not a claim to possess the server's exact retained PLAN.json.
        catalog=json.loads((HERE.parent/'native-cache-diagnostic-r1/CATALOG.json').read_bytes())
        report=json.loads((HERE.parent/'native-cache-evidence-r1/REPORT.json').read_bytes())
        plan=dict(images=[],chains={},blobs={},maximum_outer_blob_bytes=573414766)
        for image in catalog['images']:
            row=copy.deepcopy(image);row['layers']=[];parent=''
            for index in image['layer_indices']:
                layer=copy.deepcopy(catalog['layers'][index]);diff=layer['diff_id']
                chain=diff if not parent else 'sha256:'+hashlib.sha256((parent+' '+diff).encode()).hexdigest()
                layer.update(chain_id=chain,parent=parent);parent=chain
                row['layers'].append(layer);plan['chains'].setdefault(chain,layer)
                plan['blobs'][layer['blob_digest']]=layer['compressed_bytes']
            if image['role']=='ppt-master-agent-host-forward':
                diff='sha256:'+'0'*64;chain='sha256:'+hashlib.sha256((parent+' '+diff).encode()).hexdigest()
                layer=dict(chain_id=chain,parent=parent,diff_id=diff,blob_digest='sha256:'+'1'*64,
                           compressed_bytes=12345,uncompressed_bytes_upper_bound=8*1024**2)
                row['layers'].append(layer);plan['chains'][chain]=layer;plan['blobs'][layer['blob_digest']]=12345
            for key in ('manifest_digest','config_digest'):plan['blobs'][row[key]]=1024
            plan['images'].append(row)
        result=A.prepare_plan(plan,report)
        amounts=result['allocation']['physical_by_chain']
        self.assertEqual(len(amounts),94)
        self.assertEqual(sum(amounts.values()),3496771584)
        self.assertEqual(result['allocation']['required_cached_chains'],493)
        plan['images'][0]['layers'][0]['compressed_bytes']+=1
        with self.assertRaisesRegex(ValueError,'image_layer_encoding'):A.prepare_plan(plan,report)

if __name__=='__main__':unittest.main(verbosity=2)
