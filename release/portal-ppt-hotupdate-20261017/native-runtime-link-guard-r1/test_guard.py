import copy
import importlib.util
import json
from pathlib import Path
import struct
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
def load(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
G=load('link_guard');K=load('guard_kernel')
OWNER='a'*32;NS='portal-ppt-'+OWNER;UID='12345678-1234-1234-1234-123456789abc'


def pod():
    container=dict(name='app',image='fixed@sha256:'+'b'*64,imagePullPolicy='Never',
                   command=['/usr/bin/env','-i'],args=['VALUE=$(QUALIFICATION_POD_UID)','/original','space value','$(literal)',';'],
                   securityContext=dict(runAsUser=1234,allowPrivilegeEscalation=False,readOnlyRootFilesystem=True,capabilities=dict(drop=['ALL'])),
                   env=[dict(name='QUALIFICATION_POD_UID',valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid')))])
    return dict(kind='Pod',apiVersion='v1',metadata=dict(name='business',namespace=NS,labels={G.OWNER_LABEL:OWNER}),
                spec=dict(automountServiceAccountToken=False,containers=[container],volumes=[]))


def receipt():
    ns=dict(device=4,inode=4026533456,link='net:[4026533456]')
    return dict(schema='portal-owned-pod-link-guard.v1',status='OWNED_NETNS_NON_LOOPBACK_DOWN',owner=OWNER,pod_uid=UID,
                code_sha256=G.GUARD_CODE_SHA,image=dict(manifest_digest=G.GUARD_IMAGE['manifest_digest'],config_digest=G.GUARD_IMAGE['config_digest']),
                before=dict(netns=ns,interfaces=[dict(index=2,name='eth0',flags=0x1043),dict(index=1,name='lo',flags=0x49)]),
                after=dict(netns=ns,interfaces=[dict(index=2,name='eth0',flags=0x1002),dict(index=1,name='lo',flags=0x49)]),
                guard_privileges=dict(CapEff=4096,CapPrm=4096,CapBnd=4096,CapAmb=0,CapInh=0,NoNewPrivs=1),
                capabilities_revoked_by_guard=False,loopback_up=True,non_loopback_down=True,
                scope='THIS_OWNED_POD_NETWORK_NAMESPACE_ONLY')


class GuardTests(unittest.TestCase):
    def test_wrap_preserves_original_uid_argv_and_downward_expansion_tokens(self):
        original=pod();decorated=G.decorate_business_pod(original,OWNER,NS)
        original_argv=original['spec']['containers'][0]['command']+original['spec']['containers'][0]['args']
        target=decorated['spec']['containers'][0]
        self.assertEqual(target['args'][6:],original_argv)
        self.assertEqual(target['securityContext'],original['spec']['containers'][0]['securityContext'])
        self.assertEqual(target['volumeMounts'][-1],dict(name=G.RECEIPT_VOLUME,mountPath=G.RECEIPT_PATH,readOnly=True))
        self.assertEqual(G.validate_pod_guard_shape(decorated,OWNER,NS)['status'],'EXACT_GUARD_AND_PREEXEC_SHAPE')
        self.assertNotIn('initContainers',original['spec'])

    def test_guard_is_ordinary_first_before_pg_restartable_init(self):
        original=pod();pg=copy.deepcopy(original['spec']['containers'][0]);pg.update(name='postgres-fixture',restartPolicy='Always')
        original['spec']['initContainers']=[pg]
        decorated=G.decorate_business_pod(original,OWNER,NS)
        self.assertEqual([c['name'] for c in decorated['spec']['initContainers']],[G.GUARD_CONTAINER,'postgres-fixture'])
        self.assertNotIn('restartPolicy',decorated['spec']['initContainers'][0])
        self.assertEqual(decorated['spec']['initContainers'][1]['restartPolicy'],'Always')
        G.validate_pod_guard_shape(decorated,OWNER,NS)

    def test_mutated_guard_or_writable_business_receipt_or_extra_cap_rejected(self):
        base=G.decorate_business_pod(pod(),OWNER,NS)
        for variant in ('guardcap','receipt','wrapper','host','env'):
            value=copy.deepcopy(base)
            if variant=='guardcap':value['spec']['initContainers'][0]['securityContext']['capabilities']['add'].append('SYS_ADMIN')
            elif variant=='receipt':value['spec']['containers'][0]['volumeMounts'][-1]['readOnly']=False
            elif variant=='wrapper':value['spec']['containers'][0]['args'][2]='exec true'
            elif variant=='host':value['spec']['hostNetwork']=True
            else:value['spec']['containers'][0]['env'][-1]['valueFrom']['fieldRef']['fieldPath']='metadata.name'
            with self.assertRaises(ValueError):G.validate_pod_guard_shape(value,OWNER,NS)
        value=pod();value['spec']['containers'][0]['securityContext']['capabilities']['add']=['NET_ADMIN']
        with self.assertRaisesRegex(ValueError,'business_caps'):G.decorate_business_pod(value,OWNER,NS)

    def test_proof_only_subject_changes_security_and_checks_live_uid_before_guard(self):
        original=pod();original['metadata']['name']='network-subject'
        original['spec']['containers'][0].update(name='proof',image=G.GUARD_IMAGE['digest_reference'])
        decorated=G.decorate_proof_subject(original,OWNER,NS)
        expected=copy.deepcopy(original);expected['spec']['containers'][0]['securityContext']=G.GUARD_SECURITY
        self.assertEqual(decorated,expected)
        wrong=copy.deepcopy(original);wrong['metadata']['name']='network-peer'
        with self.assertRaises(ValueError):G.decorate_proof_subject(wrong,OWNER,NS)
        argv=G.proof_guard_argv(OWNER,UID)
        self.assertEqual(argv[:3],['/bin/sh','-eu','-c'])
        self.assertIn('PROOF_POD_UID',argv[3]);self.assertIn('exec "$@"',argv[3]);self.assertNotIn('eval',argv[3])
        self.assertEqual(argv[6:],G.guard_argv(OWNER,UID))

    def test_receipt_requires_exact_owner_code_image_namespace_complete_down_state(self):
        value=receipt()
        G.validate_guard_receipt(json.dumps(value).encode(),OWNER,UID,G.GUARD_IMAGE['config_digest'])
        for variant in ('owner','code','image','up','lo','namespace','duplicate','privilege'):
            item=copy.deepcopy(value)
            if variant=='owner':item['owner']='b'*32
            elif variant=='code':item['code_sha256']='0'*64
            elif variant=='image':item['image']['manifest_digest']='sha256:'+'0'*64
            elif variant=='up':item['after']['interfaces'][0]['flags']|=1
            elif variant=='lo':item['after']['interfaces'][1]['flags']&=~1
            elif variant=='namespace':item['after']['netns']=dict(device=4,inode=9,link='net:[9]')
            elif variant=='duplicate':item['after']['interfaces'][0]['index']=1
            else:item['guard_privileges']['CapBnd']+=1
            with self.assertRaises(ValueError):G.validate_guard_receipt(json.dumps(item).encode(),OWNER,UID,G.GUARD_IMAGE['config_digest'])
        with self.assertRaises(ValueError):G.validate_guard_receipt(json.dumps(value).encode(),OWNER,UID,'sha256:'+'0'*64)

    def test_complete_name_index_enumeration_includes_unaddressed_link(self):
        with mock.patch.object(K.socket,'if_nameindex',return_value=[(1,'lo'),(9,'unaddressed0'),(2,'eth0')]):
            self.assertEqual(K.topology(),[(2,'eth0'),(1,'lo'),(9,'unaddressed0')])
        with mock.patch.object(K.socket,'if_nameindex',return_value=[(1,'lo'),(1,'eth0')]):
            with self.assertRaisesRegex(RuntimeError,'interface_duplicates'):K.topology()

    def test_ioctl_uses_forty_bytes_preserves_flags_and_disables_every_nonlo(self):
        values={'eth0':0x1043,'lo':0x49,'unaddressed0':0x1043};writes=[]
        def ioctl(fd,op,raw):
            self.assertEqual(len(raw),40)
            name=raw[:16].split(b'\0')[0].decode()
            if op==K.SIOCSIFFLAGS:
                values[name]=struct.unpack_from('H',raw,16)[0];writes.append(name);return raw
            return struct.pack('16sH22x',name.encode(),values[name])
        sock=mock.Mock();sock.fileno.return_value=4
        with mock.patch.object(K,'netns',return_value=dict(device=4,inode=1,link='net:[1]')),mock.patch.object(K.socket,'if_nameindex',return_value=[(1,'lo'),(2,'eth0'),(9,'unaddressed0')]),mock.patch.object(K.fcntl,'ioctl',side_effect=ioctl):
            before,after=K.disable_links(sock)
        self.assertEqual(writes,['eth0','unaddressed0']);self.assertEqual(values,dict(eth0=0x1042,lo=0x49,unaddressed0=0x1042))

    def test_topology_change_or_initial_loopback_down_fails_closed(self):
        sock=mock.Mock()
        with mock.patch.object(K,'netns',return_value=dict(device=4,inode=1,link='net:[1]')),mock.patch.object(K,'topology',side_effect=[[(1,'lo')],[(1,'lo'),(2,'new')]]),mock.patch.object(K,'flags',return_value=9):
            with self.assertRaisesRegex(RuntimeError,'namespace_or_interfaces_changed'):K.snapshot(sock)
        with mock.patch.object(K,'netns',return_value=dict(device=4,inode=1,link='net:[1]')),mock.patch.object(K,'topology',return_value=[(1,'lo')]),mock.patch.object(K,'flags',return_value=8):
            with self.assertRaisesRegex(RuntimeError,'loopback_not_up'):K.snapshot(sock)


if __name__=='__main__':unittest.main(verbosity=2)
