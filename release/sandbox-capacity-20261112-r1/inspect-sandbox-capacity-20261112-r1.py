"""Readonly native Kubernetes requested-resource and sandbox-template snapshot."""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

ASSETS={'child': {'base64': 'IiIiT25seSBmaXhlZCBQcm92aXNpb25lciBzb3VyY2UgQVNUIGFuZCByZXNvdXJjZS9ub2RlLXBvb2wgZW52aXJvbm1lbnQgaW5wdXRzLiIiIgppbXBvcnQgYXN0CmltcG9ydCBoYXNobGliCmltcG9ydCBqc29uCmltcG9ydCBvcwpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgKaW1wb3J0IHJlCmltcG9ydCBzdGF0CmltcG9ydCBzeXMKCktFWVM9KCdTQU5EQk9YX05PREVfUE9PTCcsJ1NBTkRCT1hfQ1BVX1JFUVVFU1QnLCdTQU5EQk9YX01FTU9SWV9SRVFVRVNUJywnU0FOREJPWF9DUFVfTElNSVQnLCdTQU5EQk9YX01FTU9SWV9MSU1JVCcsCiAgICAgICdTQU5EQk9YX0VQSEVNRVJBTF9TVE9SQUdFX1JFUVVFU1QnLCdTQU5EQk9YX0VQSEVNRVJBTF9TVE9SQUdFX0xJTUlUJywnUFBUX01BU1RFUl9BR0VOVF9IT1NUX0VOQUJMRUQnLAogICAgICAnUFBUX01BU1RFUl9BR0VOVF9IT1NUX0NQVV9SRVFVRVNUJywnUFBUX01BU1RFUl9BR0VOVF9IT1NUX01FTU9SWV9SRVFVRVNUJywnUFBUX01BU1RFUl9BR0VOVF9IT1NUX0NQVV9MSU1JVCcsJ1BQVF9NQVNURVJfQUdFTlRfSE9TVF9NRU1PUllfTElNSVQnKQpOQU1FUz17J3NhbmRib3gnLCdwcHQtbWFzdGVyLWFnZW50LWhvc3QnLCdwcmVwYXJlLXVzZXItZGF0YScsJ3ZlcmlmeS1za2lsbC1tb3VudHMnfQpSRVNPVVJDRVM9eydjcHUnLCdtZW1vcnknLCdlcGhlbWVyYWwtc3RvcmFnZSd9CgpkZWYgc2FmZV9pbnB1dChrZXksdmFsdWUpOgogICAgaWYgdmFsdWUgaXMgTm9uZTpyZXR1cm4gTm9uZQogICAgaWYgbm90IGlzaW5zdGFuY2UodmFsdWUsc3RyKSBvciBsZW4odmFsdWUpPjQwOTY6cmFpc2UgVmFsdWVFcnJvcignUkVTT1VSQ0VfRU5WX1NIQVBFJykKICAgIGlmIGtleT09J1NBTkRCT1hfTk9ERV9QT09MJzoKICAgICAgICBpZiB2YWx1ZSBhbmQgbm90IGFsbChyZS5mdWxsbWF0Y2goJ1thLXowLTldKD86W2EtejAtOS4tXXswLDI1MX1bYS16MC05XSk/JyxwYXJ0LnN0cmlwKCkpIGZvciBwYXJ0IGluIHZhbHVlLnNwbGl0KCcsJykpOnJhaXNlIFZhbHVlRXJyb3IoJ05PREVfUE9PTF9TSEFQRScpCiAgICBlbGlmIGtleT09J1BQVF9NQVNURVJfQUdFTlRfSE9TVF9FTkFCTEVEJzoKICAgICAgICBpZiB2YWx1ZS5zdHJpcCgpLmxvd2VyKCkgbm90IGluICgndHJ1ZScsJ2ZhbHNlJywnMScsJzAnLCd5ZXMnLCdubycsJ29uJywnb2ZmJywnJyk6cmFpc2UgVmFsdWVFcnJvcignQk9PTEVBTl9FTlZfU0hBUEUnKQogICAgZWxpZiBub3QgcmUuZnVsbG1hdGNoKHInWzAtOV0rKD86XC5bMC05XSspPyg/Om18S2l8TWl8R2l8VGl8S3xNfEd8VCk/Jyx2YWx1ZSk6cmFpc2UgVmFsdWVFcnJvcignUkVTT1VSQ0VfRU5WX1FVQU5USVRZJykKICAgIHJldHVybiB2YWx1ZQoKZGVmIHNjYWxhcihub2RlLHJlc291cmNlPUZhbHNlKToKICAgIGlmIGlzaW5zdGFuY2Uobm9kZSxhc3QuQ29uc3RhbnQpIGFuZCBpc2luc3RhbmNlKG5vZGUudmFsdWUsKHN0cixpbnQsZmxvYXQsYm9vbCx0eXBlKE5vbmUpKSk6CiAgICAgICAgaWYgcmVzb3VyY2U6CiAgICAgICAgICAgIGlmIGlzaW5zdGFuY2Uobm9kZS52YWx1ZSxib29sKSBvciBub2RlLnZhbHVlIGlzIE5vbmU6cmV0dXJuIHsna2luZCc6J3Vua25vd25fZXhwcmVzc2lvbid9CiAgICAgICAgICAgIHNhZmVfaW5wdXQoJ1NBTkRCT1hfQ1BVX1JFUVVFU1QnLHN0cihub2RlLnZhbHVlKSkKICAgICAgICByZXR1cm4geydraW5kJzonbGl0ZXJhbCcsJ3ZhbHVlJzpub2RlLnZhbHVlfQogICAgaWYgaXNpbnN0YW5jZShub2RlLGFzdC5OYW1lKSBhbmQgbm9kZS5pZCBpbiBLRVlTOnJldHVybiB7J2tpbmQnOidyZXNvdXJjZV92YXJpYWJsZScsJ25hbWUnOm5vZGUuaWR9CiAgICByZXR1cm4geydraW5kJzondW5rbm93bl9leHByZXNzaW9uJ30KCmRlZiBzb3VyY2Vfc3VtbWFyeShyYXcsZW52aXJvbm1lbnQpOgogICAgdHJlZT1hc3QucGFyc2UocmF3KTtkZWZhdWx0cz17fTtjb250YWluZXJzPVtdCiAgICBmb3Igbm9kZSBpbiBhc3Qud2Fsayh0cmVlKToKICAgICAgICBpZiBpc2luc3RhbmNlKG5vZGUsYXN0LkNhbGwpIGFuZCBpc2luc3RhbmNlKG5vZGUuZnVuYyxhc3QuQXR0cmlidXRlKSBhbmQgbm9kZS5mdW5jLmF0dHI9PSdnZXQnIGFuZCBpc2luc3RhbmNlKG5vZGUuZnVuYy52YWx1ZSxhc3QuQXR0cmlidXRlKSBhbmQgaXNpbnN0YW5jZShub2RlLmZ1bmMudmFsdWUudmFsdWUsYXN0Lk5hbWUpIGFuZCBub2RlLmZ1bmMudmFsdWUudmFsdWUuaWQ9PSdvcycgYW5kIG5vZGUuZnVuYy52YWx1ZS5hdHRyPT0nZW52aXJvbic6CiAgICAgICAgICAgIGtleT1ub2RlLmFyZ3NbMF0udmFsdWUgaWYgbm9kZS5hcmdzIGFuZCBpc2luc3RhbmNlKG5vZGUuYXJnc1swXSxhc3QuQ29uc3RhbnQpIGVsc2UgTm9uZQogICAgICAgICAgICBpZiBrZXkgaW4gS0VZUzoKICAgICAgICAgICAgICAgIHZhbHVlPXNjYWxhcihub2RlLmFyZ3NbMV0pIGlmIGxlbihub2RlLmFyZ3MpPjEgZWxzZSB7J2tpbmQnOidsaXRlcmFsJywndmFsdWUnOk5vbmV9CiAgICAgICAgICAgICAgICBpZiB2YWx1ZS5nZXQoJ2tpbmQnKT09J2xpdGVyYWwnIGFuZCBpc2luc3RhbmNlKHZhbHVlLmdldCgndmFsdWUnKSxzdHIpOnZhbHVlWyd2YWx1ZSddPXNhZmVfaW5wdXQoa2V5LHZhbHVlWyd2YWx1ZSddKQogICAgICAgICAgICAgICAgZGVmYXVsdHMuc2V0ZGVmYXVsdChrZXksW10pLmFwcGVuZCh2YWx1ZSkKICAgICAgICBpZiBpc2luc3RhbmNlKG5vZGUsYXN0LkNhbGwpIGFuZCBpc2luc3RhbmNlKG5vZGUuZnVuYyxhc3QuQXR0cmlidXRlKSBhbmQgbm9kZS5mdW5jLmF0dHI9PSdWMUNvbnRhaW5lcic6CiAgICAgICAgICAgIGtleXdvcmRzPXtpdGVtLmFyZzppdGVtLnZhbHVlIGZvciBpdGVtIGluIG5vZGUua2V5d29yZHMgaWYgaXRlbS5hcmd9CiAgICAgICAgICAgIG5hbWU9a2V5d29yZHMuZ2V0KCduYW1lJyk7bmFtZT1uYW1lLnZhbHVlIGlmIGlzaW5zdGFuY2UobmFtZSxhc3QuQ29uc3RhbnQpIGVsc2UgTm9uZQogICAgICAgICAgICBpZiBuYW1lIG5vdCBpbiBOQU1FUzpjb250aW51ZQogICAgICAgICAgICByZXN1bHQ9eydjb250YWluZXInOm5hbWV9O3Jlc291cmNlcz1rZXl3b3Jkcy5nZXQoJ3Jlc291cmNlcycpCiAgICAgICAgICAgIGlmIGlzaW5zdGFuY2UocmVzb3VyY2VzLGFzdC5DYWxsKToKICAgICAgICAgICAgICAgIGZvciBpdGVtIGluIHJlc291cmNlcy5rZXl3b3JkczoKICAgICAgICAgICAgICAgICAgICBpZiBpdGVtLmFyZyBub3QgaW4gKCdyZXF1ZXN0cycsJ2xpbWl0cycpIG9yIG5vdCBpc2luc3RhbmNlKGl0ZW0udmFsdWUsYXN0LkRpY3QpOmNvbnRpbnVlCiAgICAgICAgICAgICAgICAgICAgcmVzdWx0W2l0ZW0uYXJnXT17a2V5LnZhbHVlOnNjYWxhcih2YWx1ZSxyZXNvdXJjZT1UcnVlKSBmb3Iga2V5LHZhbHVlIGluIHppcChpdGVtLnZhbHVlLmtleXMsaXRlbS52YWx1ZS52YWx1ZXMpIGlmIGlzaW5zdGFuY2Uoa2V5LGFzdC5Db25zdGFudCkgYW5kIGtleS52YWx1ZSBpbiBSRVNPVVJDRVN9CiAgICAgICAgICAgIGNvbnRhaW5lcnMuYXBwZW5kKHJlc3VsdCkKICAgIHJldHVybiB7J2Vudmlyb25tZW50X2lucHV0cyc6e2tleTp7J3ByZXNlbnQnOmtleSBpbiBlbnZpcm9ubWVudCwndmFsdWUnOnNhZmVfaW5wdXQoa2V5LGVudmlyb25tZW50LmdldChrZXkpKSwnc291cmNlX2RlZmF1bHRzJzpkZWZhdWx0cy5nZXQoa2V5LFtdKX0gZm9yIGtleSBpbiBLRVlTfSwKICAgICAgICAnY29udGFpbmVyX3Jlc291cmNlX2V4cHJlc3Npb25zJzpjb250YWluZXJzLCdkZWZhdWx0c19iYXNpcyc6J0FTVF9MSVRFUkFMX09OTFlfTk9fQVBQTElDQVRJT05fSU1QT1JUX09SX1RFTVBMQVRFX0VYRUNVVElPTid9CgpkZWYgbWFpbigpOgogICAgc3lzLmRvbnRfd3JpdGVfYnl0ZWNvZGU9VHJ1ZTtvdXQ9eydzY2hlbWEnOidzYW5kYm94LWNhcGFjaXR5LXByb3Zpc2lvbmVyLXNvdXJjZS52MScsJ3N0YXR1cyc6J0lOQ09NUExFVEUnLCdmaWxlc19tb2RpZmllZCc6RmFsc2UsJ2NyZWRlbnRpYWxzX2V4cG9ydGVkJzpGYWxzZSwnc2VjcmV0X2FwaV9yZWFkcyc6MCwnYXBwbGljYXRpb25faW1wb3J0ZWQnOkZhbHNlLCdwcm92aWRlcl9yZXF1ZXN0cyc6MH0KICAgIHRyeToKICAgICAgICBwYXRoPVBhdGgoJy9hcHAvYXBwLnB5Jyk7ZmQ9b3Mub3BlbihzdHIocGF0aCksb3MuT19SRE9OTFl8b3MuT19OT0ZPTExPV3xvcy5PX05PTkJMT0NLKQogICAgICAgIHdpdGggb3MuZmRvcGVuKGZkLCdyYicpIGFzIHN0cmVhbToKICAgICAgICAgICAgYmVmb3JlPW9zLmZzdGF0KHN0cmVhbS5maWxlbm8oKSkKICAgICAgICAgICAgaWYgbm90IHN0YXQuU19JU1JFRyhiZWZvcmUuc3RfbW9kZSkgb3IgYmVmb3JlLnN0X3NpemU+MTMxMDcyOnJhaXNlIFZhbHVlRXJyb3IoJ1NPVVJDRV9TSVpFX09SX1RZUEUnKQogICAgICAgICAgICByYXc9c3RyZWFtLnJlYWQoMTMxMDczKTthZnRlcj1vcy5mc3RhdChzdHJlYW0uZmlsZW5vKCkpCiAgICAgICAgc2lnbmF0dXJlPWxhbWJkYSBpdGVtOihpdGVtLnN0X2RldixpdGVtLnN0X2lubyxpdGVtLnN0X3NpemUsaXRlbS5zdF9tdGltZV9ucyxpdGVtLnN0X2N0aW1lX25zKQogICAgICAgIGlmIGxlbihyYXcpPjEzMTA3MiBvciBzaWduYXR1cmUoYmVmb3JlKSE9c2lnbmF0dXJlKGFmdGVyKSBvciBzaWduYXR1cmUocGF0aC5sc3RhdCgpKSE9c2lnbmF0dXJlKGFmdGVyKTpyYWlzZSBWYWx1ZUVycm9yKCdTT1VSQ0VfQ0hBTkdFRCcpCiAgICAgICAgb3V0LnVwZGF0ZShzb3VyY2Vfc2hhMjU2PWhhc2hsaWIuc2hhMjU2KHJhdykuaGV4ZGlnZXN0KCksc291cmNlX2J5dGVzPWxlbihyYXcpLCoqc291cmNlX3N1bW1hcnkocmF3LHtrZXk6b3MuZW52aXJvbltrZXldIGZvciBrZXkgaW4gS0VZUyBpZiBrZXkgaW4gb3MuZW52aXJvbn0pLHN0YXR1cz0nUFJPVklTSU9ORVJfUkVTT1VSQ0VfSU5QVVRTX09CU0VSVkVEX05PX0NIQU5HRScpCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGV4YzpvdXQudXBkYXRlKGVycm9yX2NsYXNzPXR5cGUoZXhjKS5fX25hbWVfXyxlcnJvcl9jb2RlPXN0cihleGMpIGlmIGlzaW5zdGFuY2UoZXhjLFZhbHVlRXJyb3IpIGVsc2UgJ1NPVVJDRV9PQlNFUlZBVElPTl9GQUlMRURfUkVEQUNURUQnKQogICAgcHJpbnQoanNvbi5kdW1wcyhvdXQsc29ydF9rZXlzPVRydWUpKTtyZXR1cm4gaW50KG91dFsnc3RhdHVzJ109PSdJTkNPTVBMRVRFJykKCmlmIF9fbmFtZV9fPT0nX19tYWluX18nOnN5cy5leGl0KG1haW4oKSkK', 'sha256': '0c2bc2662c6e937f9943335fe21905f314a58bc3886f02f88912b895b1dc9d84'}}
PROVISIONER_UID='9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253'
EXPECTED_IMAGE='sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
NAMED_THREADS=('201fb3d5-49c0-4276-9e3f-3db67f8edfe7','0fb37c34-d996-4d8b-aeeb-ea9dcd252a28')
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
SANDBOX_LIST=['get','pods','-l','app.kubernetes.io/component=sandbox','--no-headers','-o',
 'custom-columns=NAME:.metadata.name,UID:.metadata.uid,NODE:.spec.nodeName,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="sandbox")].ready,CREATED:.metadata.creationTimestamp,SUBPATHS:.spec.containers[?(@.name=="sandbox")].volumeMounts[*].subPath']

class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()

def child_source():
    need(set(ASSETS)=={'child'},'ASSETS_NOT_SEALED');row=ASSETS['child'];raw=base64.b64decode(row['base64'],validate=True)
    need(len(raw)<=65536 and sha(raw)==row['sha256'],'CHILD_RESOURCE_CHANGED');compile(raw,'<fixed-resource-reader>','exec');return raw.decode()

def pod_identity(pod):
    meta=pod['metadata'];status=pod.get('status',{});states=status.get('containerStatuses',[])
    need(meta.get('name','').startswith('provisioner-') and not meta.get('deletionTimestamp') and status.get('phase')=='Running' and states and all(row.get('ready') is True for row in states),'READY_PROVISIONER_REQUIRED')
    return {'name':meta['name'],'uid':meta['uid'],'spec_sha256':sha(canonical(pod['spec'])),'container_state_sha256':sha(canonical([{key:row.get(key) for key in ('name','containerID','imageID','restartCount')} for row in states]))}

def owned_pod(pods,dep):
    items=pods.get('items');need(isinstance(items,list) and len(items)<=32,'PROVISIONER_POD_LIST_BOUND')
    found=[]
    for pod in items:
        try:identity=pod_identity(pod)
        except Refused:continue
        owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet' and re.fullmatch('provisioner-[a-z0-9-]+',owners[0].get('name','')),'PROVISIONER_RS_REQUIRED')
        found.append((pod,identity,owners[0]))
    need(len(found)==1,'ONE_READY_PROVISIONER_REQUIRED');return found[0]

def verify_owner(owner,rs):
    need(rs['metadata'].get('name')==owner['name'] and rs['metadata'].get('uid')==owner['uid'],'PROVISIONER_RS_CHANGED')
    controllers=[row for row in rs['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
    need(len(controllers)==1 and controllers[0].get('kind')=='Deployment' and controllers[0].get('uid')==PROVISIONER_UID,'PROVISIONER_OWNER_CHANGED')

def summarize_nodes(value):
    items=value.get('items');need(isinstance(items,list) and 0<len(items)<=64,'NODE_COUNT_BOUND');result=[]
    for node in items:
        meta,spec,status=node['metadata'],node.get('spec',{}),node.get('status',{})
        ready=next((row.get('status') for row in status.get('conditions',[]) if row.get('type')=='Ready'),'Unknown')
        result.append({'name':meta['name'],'hostname':meta.get('labels',{}).get('kubernetes.io/hostname'),'architecture':meta.get('labels',{}).get('kubernetes.io/arch'),
            'role_or_workload_labels':{key:value for key,value in meta.get('labels',{}).items() if key.startswith('node-role.kubernetes.io/') or key in ('workload','workload-type','deerflow.io/workload','deerflow.io/node-role','deerflow.ai/workload','deerflow.ai/node-role')},
            'ready':ready,'unschedulable':spec.get('unschedulable',False),'taints':[{key:row.get(key) for key in ('key','value','effect')} for row in spec.get('taints',[])],
            'allocatable':{key:status.get('allocatable',{}).get(key) for key in ('cpu','memory','pods','ephemeral-storage')}})
    return sorted(result,key=lambda row:row['name'])

def native_allocations(raw,names):
    need(isinstance(raw,str) and len(raw.encode())<=4194304,'DESCRIBE_NODE_BOUND');result=[];current=None;section=None
    for line in raw.splitlines():
        match=re.match(r'^Name:\s+(\S+)\s*$',line)
        if match:
            need(match.group(1) in names,'DESCRIBE_NODE_SCOPE');current={'name':match.group(1),'allocated_resources':{},'native_nonterminated_pod_count':None};result.append(current);section=None;continue
        if current is None:continue
        match=re.match(r'^Non-terminated Pods:\s+\((\d+) in total\)',line)
        if match:current['native_nonterminated_pod_count']=int(match.group(1));section=None;continue
        if line.startswith('Allocated resources:'):section='allocated';continue
        if line and not line[0].isspace():section=None
        if section=='allocated':
            columns=re.split(r'\s{2,}',line.strip())
            if len(columns)==3 and columns[0] in ('cpu','memory','ephemeral-storage'):
                need(all(re.fullmatch(r'[0-9A-Za-z.+-]+(?: \([0-9.]+%\))?',value) for value in columns[1:]),'NATIVE_RESOURCE_CELL')
                current['allocated_resources'][columns[0]]={'requests_native':columns[1],'limits_native':columns[2]}
    need({row['name'] for row in result}==set(names) and len(result)==len(names) and all({'cpu','memory'}<=set(row['allocated_resources']) and row['native_nonterminated_pod_count'] is not None for row in result),'NATIVE_ALLOCATION_SECTIONS_REQUIRED')
    return result

def sandbox_rows(raw):
    need(isinstance(raw,str) and len(raw.encode())<=262144,'SANDBOX_LIST_BOUND');rows=[];counts={}
    for line in raw.splitlines():
        values=line.split();need(len(values)==8,'SANDBOX_ROW_SHAPE')
        name,uid,node,phase,deleting,ready,created,subpaths=values
        need(re.fullmatch('sandbox-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'SANDBOX_ID_SHAPE')
        matches=[thread for thread in NAMED_THREADS if any(path.endswith('/threads/'+thread+'/user-data') for path in subpaths.split(','))]
        row={'name':name,'uid':uid,'node':node,'phase':phase,'deleting':deleting!='<none>','ready':ready=='true','created_at':created,'named_thread_matches':matches};rows.append(row)
        key=node if node!='<none>' else 'unassigned';summary=counts.setdefault(key,{'total':0,'ready':0,'pending':0,'deleting':0});summary['total']+=1;summary['ready']+=int(row['ready']);summary['pending']+=int(phase=='Pending');summary['deleting']+=int(row['deleting'])
    need(len(rows)<=2048,'SANDBOX_COUNT_BOUND');return rows,counts

def sandbox_template(pod,chosen):
    meta,spec,status=pod['metadata'],pod['spec'],pod.get('status',{})
    need(meta.get('uid')==chosen['uid'] and meta.get('name')==chosen['name'] and not meta.get('deletionTimestamp'),'SANDBOX_SAMPLE_CHANGED')
    states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
    need(len(states)==1 and states[0].get('ready') is True and states[0].get('imageID','').endswith(EXPECTED_IMAGE),'EXPECTED_ORDINARY_SANDBOX_REQUIRED')
    containers=lambda rows:[{'name':row.get('name'),'resources':row.get('resources',{}),'restart_policy':row.get('restartPolicy')} for row in rows]
    return {'sample_name':meta['name'],'uid':meta['uid'],'node':spec.get('nodeName'),'containers':containers(spec.get('containers',[])),'init_containers':containers(spec.get('initContainers',[])),
        'sandbox_container_ready':True,'pod_ready':any(row.get('type')=='Ready' and row.get('status')=='True' for row in status.get('conditions',[])),
        'affinity':spec.get('affinity'),'node_selector':spec.get('nodeSelector'),'tolerations':spec.get('tolerations',[]),'overhead':spec.get('overhead',{}),'pod_level_resources':spec.get('resources'),
        'pvc_claims':[row['persistentVolumeClaim']['claimName'] for row in spec.get('volumes',[]) if isinstance(row.get('persistentVolumeClaim'),dict)],
        'environment_or_commands_exported':False,'sample_is_not_proof_every_sandbox_has_identical_template':True}

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.sandbox,self.queries=child,None,None,None,0
    def command(self,args):
        allowed=args in (['get','deployment','provisioner','-o','json'],['get','pods','-l','app=provisioner','-o','json'],['get','nodes','-o','json'],['describe','nodes'],SANDBOX_LIST)
        if self.pod:allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec',self.pod,'-c','provisioner','--','python3','-I','-B','-c',self.child]
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        if self.sandbox:allowed=allowed or args==['get','pod',self.sandbox,'-o','json']
        need(allowed,'FIXED_READONLY_COMMAND_REQUIRED');need(self.queries<9,'QUERY_BOUND');self.queries+=1
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(KUBE+args,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+25
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=4194304 and os.fstat(errors.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic()<until,'COMMAND_DEADLINE');time.sleep(.1)
                need(process.returncode==0,'READ_COMMAND_FAILED');need(os.fstat(output.fileno()).st_size<=4194304 and os.fstat(errors.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                output.seek(0);return output.read(4194305).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    op=None;out={'schema':'sandbox-scheduling-capacity.v1','status':'INCOMPLETE','workload_modified':False,'files_modified':False,'database_access':False,'secret_api_reads':0,
        'credentials_exported':False,'provider_requests':0,'sandboxes_created':0,'sandboxes_deleted':0,'execution_leases_read_or_acquired':False,
        'idle_state_not_inferred_from_pod_age':True,'headroom_formula_not_reimplemented':True,'existing_business_tasks_preserved':True}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(child_source());dep=json.loads(op.command(['get','deployment','provisioner','-o','json']))
        need(dep['metadata'].get('uid')==PROVISIONER_UID,'PROVISIONER_DEPLOYMENT_CHANGED')
        out['provisioner']={'uid':PROVISIONER_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':sha(canonical(dep['spec']))}
        pod,before,owner=owned_pod(json.loads(op.command(['get','pods','-l','app=provisioner','-o','json'])),dep);op.pod=before['name'];op.rs=owner['name']
        verify_owner(owner,json.loads(op.command(['get','replicaset',op.rs,'-o','json'])))
        out['provisioner_sample']=before;observed=json.loads(op.command(['exec',op.pod,'-c','provisioner','--','python3','-I','-B','-c',op.child]));out['provisioner_inputs']=observed
        need(observed.get('status')=='PROVISIONER_RESOURCE_INPUTS_OBSERVED_NO_CHANGE','PROVISIONER_SOURCE_INCOMPLETE')
        nodes=summarize_nodes(json.loads(op.command(['get','nodes','-o','json'])));out['nodes']=nodes
        out['native_node_allocations']=native_allocations(op.command(['describe','nodes']),[row['name'] for row in nodes])
        rows,counts=sandbox_rows(op.command(SANDBOX_LIST));out.update(sandbox_count=len(rows),sandboxes_by_node=counts)
        out['sandbox_projection_readiness_basis']='SANDBOX_CONTAINER_ONLY_NOT_POD_READY'
        out['named_thread_existing_sandboxes']={thread:[{**{key:row[key] for key in ('name','node','phase','deleting')},'sandbox_container_ready':row['ready']} for row in rows if thread in row['named_thread_matches']] for thread in NAMED_THREADS}
        out['userdata_subpath_or_owner_exported']=False
        ready=sorted((row for row in rows if row['ready'] and not row['deleting'] and row['phase']=='Running'),key=lambda row:(NAMED_THREADS[0] not in row['named_thread_matches'],row['name']))
        if ready:
            chosen=ready[0];op.sandbox=chosen['name'];out['ordinary_sandbox_template']=sandbox_template(json.loads(op.command(['get','pod',op.sandbox,'-o','json'])),chosen)
        else:out['ordinary_sandbox_template_unavailable']=True
        need(before==pod_identity(json.loads(op.command(['get','pod',op.pod,'-o','json']))),'PROVISIONER_CHANGED_DURING_READ')
        out.update(status='SANDBOX_NATIVE_CAPACITY_AND_TEMPLATE_OBSERVED_NO_CHANGE',stable_provisioner_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'CAPACITY_INSPECTION_FAILED_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat());print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
