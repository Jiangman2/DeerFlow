"""Pure owned-Pod link guard decoration and receipt checks; no lifecycle client."""
import base64
import copy
import hashlib
import json
import re
import zlib

GUARD_CODE_SHA='d78e8e2778f35f017f49285b2723303d113647a7e8e07e31e00b5035c85a7a2f'
GUARD_Z64='eNqlWFtT40YWfvevUNgikiuyRja2sUm0Ww6YGSqAKSA1m3VcXS2phTsjS161zCUs+9v3nG61LrZJTbJ+APXt3C9f98HBwRkX1I+ZkaRJJ07TtU+DL0bMky/C4ImRL7kw0qeEhcZNGhoJyxPxvSHyMOa+kSbxi3NwcNDiq3Wa5QbNHtY0E0yPoyDJYz1YUrGEQ3r4m0gT/Z0K/ZWVZ0UafGF5Ocpp7TvbBNXoRbRaV5Pri/Pp3b3hGaZY0t5geDL2j+gwHLlhz/WPjwajQTAc9nu97pF7fNw7Gvr+eNwfRUeD0A0HIaW9YY8dj8Juz4VNzB8zs3U6uz6/+FgjOfD7R34wDsJBNOi6vbE/OqY0cv1etxe4w36312e9ceSPuv1gMICtxyE7DkfD/qjbi3pjt2e27k4/Ta8mSBJlp3FHWrazTsMOWrzzsKFZ6Dx2YefF7PTjxfn55eTjnW3g6K4YwWn3eTTuHtnqf78FC+TnG9vA/5ez2c2Pk9OfYFfXNkatVitkEZj13xueMeuRxhtmG0EasvZJy4Afj8DxuSEX1Az+MsoFM243Sc5XbJplaWbJMwU5GQWWppBEKXBLhYNOsswP6ywNPggWRx8S8QG2mm25DxVU+zJGQxy9v1fLq86AuWDpZH4YLkzjUDIEXoQnqS1XEkFwI06ErKSQb7LECHmQWyF75AHz9DkY2obc6zVIIQ0P/2g183SdxunDi9Y0S58EaKAi0+ERSeiK8SRkz1ZTbLmRJqERs0QO2sYPnjHsg7g8yVkW0YCRIAXzbulL49jKX9bMklTbBscczCUpOWP83XDVSPAEzJ0EzEIhbEyKduk+/cOdGXOiTRyvaB4srcycTzr/op3f3c6YOCedxWvX7g7eTNtAKm0jSjPFSE1gAZDiNwSHbFizbUeBoq9KxBoNogm8tdGLlTW0bV4lFzxBmhy3DjT4h5t1zAOaM9H0tYCUYqE8YBtf2IsX05UfUoPnbHWCf+bdhXZtFNMHYaEnC9WVh/9mXPJk8wxZAYqh9fuu4b8AJyh1hp/mSyOHGsdyY3J7NewrNdKAxsbzaEhgIuLPIAoTTmkcJnIMGVmynDWUVsvsDsWnXu+5MLrDEkwty6Qi4NwEVd1d0xaUpFH6LloDBSSC/145QqzTBJLWU1XX4WmQx1JDJ+JAI7Xaqo5UVUVT3cNOEdvmp+ebjJXxlYabBHUkUZauLPOTaZdy2UZ32J67i8L+IqFrsUxzKWBhfJ9BIKACRXmRkyzJM84w7apsrCfjXGa4jDevFrmeygrpZq/mbOnrepzroCv4LBq2qFiiIbQo6PVCRJxWYmMlAlJijRGaZqQMV0GCJU0eWLiVMWKzwlCdy2PmwvPMODWlYDBbJh4ygDpubhIOviJxSorz4Z7K4adprGhKjc2F8W2jJ0hqu0zbO1yBoUYCBEkRHoLyPH/ZYpqw5xzpbRPArrLLZlssbFkVH+hCZLPeU72lqT1t5cqunhS1iCctoOpwul4rKnOlsvS3ubBrgjU0lyfnVZ0BQXW0hgoiyS4j9oZsM57lWo202takXTXbprGMbzxDhoIs3rvO/PnmpFHnK19sReRctUY4iJSaoVyaS21tf3X06l+wyTIggdWmSq66ZRu7369IdWBj76+SNao7tVKL8a3xX2WbtmJMI5B/v1vKhMEtpY08b5/RSjPJzW3P+8t2q6fpXqeiV9x6zBTy1UNmK1K+UWmFMqQJKRNJ5DyO66lU1JuGKZCd5GAjFAUEJJEn0ZvIBtEFhHwzH3USypNFbgCCJSLdZAoYlFCJPiHWW0MzqYO8YIXAj5m2mflmWwJBC5B3tz/Y04ToU/sHT64icIK++0h8wEzapDgBPGCbIwAQ5JZv/vrsutsGh12OhFqw3gnMsqLyBCXRouO2pq7FdcVR2F/SmUtiCvApYt8BonCW7DnkD9BIrXbNJuuMP0KUw7y2yRMFX4beq3lK19MoAiPAx022Uh8/gmLyY7Ly1cdFsoSP6/SaPd0AMWG+STKyTgnvVY2eOICSHTsjEt8APDKowM7M6KoqGhhjqDoG2fYa/gA52cSWXBANM8jHDGKep8DiZKsMQEDCdqSklDvZgaDaD7gNLxq60AJEM0sgVxmLRJzF28Wm0noOZBbQ53NV5wG6Z3wNZaTrFqJAq6kZzGAxICIAHo2QKAzoye6ifOH13fHQVu4ov8Ej5Tc4xXNt5RT4qHh4XUxAlT2VFkEKWIEGeTOkFOMiRp4ygKTQzQPG13BFAcGCPM1eEDHJqZ3bVSyvV+XGreyGNeeOXNydXdxa+lazwitbcXlQMxseep66QaAzkCqHuzzPanRBnUIEIAB5IzszwD+xLLRRaoBQ0oB4g8284sTclEPosnClldzKhWIClrCEE5VW1XJtErbsuN9QiIugnp55GJ4chngVtMrzsiSZi7J+z01168OG//4edWNctN9nKC+Ff0AB1/cLXEMq5q+J6fyWcsigw9A4FEr0rwElu5zrDaFdd8jcvJ3ezG7vHXxZgWbhyScWJ9ys1kLbycY7EoE8Ed59tmG2YJDcFNwuPAtqjY0JXqEXiaEl8SptHbxIlSVN7gxVfMoiBP/XNF8qZauglrDbhsUZ+Xw7u7785T/y+/R2OrlXn9N/nl6qr+vZ+ezycvbZdtN+v19VAlXphBOFklMU2uaTv7/E4U/NOTLNLJXz36Ef2hpEtNvf601RvBFLC8ZIXrwkgaUXCpxS2OQrNAUnTM5+Mf8fZf+Eog0lffN0dnVzOb2fopp/VjlYDZZQMWq6uOlgMNA9bUVBz8Lt8pEv8/RznzPJHjYrQEY3cr64oKlNDg1D7K5y3TI7HVUgbH2NkXEIwr2zG1/GsGps7f8jBujcDlSSr+ZR5EanVLwCGMIrzsh/eEpsPfWIF7AqYjmHizSGXhbK6lpNJynclnO2NRtCgyCqBeD7AsqMmKQgQdT7KlEgsckQ/PTAcgZGsdq6ljfeeMw57URuZ7x4Peq9mTbK7EijN5vqvrchfXD0Zv3jpFMO+2/t16O3atwt6RZFvWqB71wUt0Gi58njuujvwDGFpwoiFZLytoFVlS/Fo5z6ZxWjyTm5uJ7e28Xwbnb6Ezn7eDu5UskEs1UqFdhW1llvz4Wv0EeGioIOIliyFfXUm66tIJdnzj5fT88IcL2+g+y+Li/f5AxWTFu1y8opZaOs23NPO6k3zabt+Io+MCXRiiY8YvjEKe3n6WdxaLlJxB/0tHrY3tf0lBG8hi3UNaG8XmzWqnU07hwh6KKmd2luYSNIqfJzn550TX0OcJ7DHSpjj+kXSAf/Rd1PvHMKgM4WARRGz7z/dHFHlLlvZtLkn2e3P5HrydX07mZyOiVYe4sYAnQoraaRTZnrVQA0Adn+3RpLlIEJFeQvdllb1uWinLVaICCR78mEAIYlBMstIaYST9Xe1v8AMKpEzQ=='
WRAPPER_SHA='19221053372835b07e4897d0483d192fd8d14f879e70d752cfd8bee6d2ebd210'
WRAPPER_Z64='eNq9Vm1vo0YQ/s6vmBKfbXyi2G4bXeIS1bVJG9VNrFyivqQR2sASUPDiskvia+z/3h1YMNi+nPql+RDwvD6z88ywnAowaaYlafQYMRK7SyJCl1Nht17n45uf3682TZ2SmzVxnHgkpsppNnHHs1nDrdBXOumKEWwr46n1ELFT/KcVOnui0dUySQWgCRRCLSBR3DXgFZZpxEQAnXf8L9aBztVvl87UnV9N3dnF5S/u/Npxfncm7rVzfvvRmXbgrD0cAV1FAk4GI9hod6C3jnQwHyl8A/ewXgNG1pIXRlO7NdC8xJdlhMRuDTUeRoGAoZZSj0ZLYVuIisTmcinMOGJP5mNGUl8LkhREksQQMfCIgJQSH9XABREj8BPwksWCMB/MZ5kdTXU4s3z6bLEsjmF41h6UQNCcUYnSZNL0dT6/cX+6HV8XFd5eTE/NjV6DjdV0MafeUiCta2c8/UM3dLBhcvXrfObcOG875KUXDnqr+PG2wzLx3SzyS5c9jF9wL094+N1xGaIU1T0lB6gnqO8ybu+GYFQw7uLx6sa+A579Z1xQdchFUoqmAfHofq6tqubohdR7ksiQkRpAXiXCAXPmSWr6p++iDljLNPEsTuPAYhwBqGprldULVmEq9nzRPS+mFmCjYHlkWQLjlDK7L19ewiimcHH+0T7N+QlmCk/0EzyTOKNIOmkDkr2cygxSoUsy5yKACVk6QbCWj3m6wMePzMfHePGAjwsWGsoS88kjMKGVh62k5czRv2HQKLj4U1kHmBM6nXXv7qv+fc8o5kEOLydeLYEsqNXt4hPew8AwRiOlvEwu6cs8jZ75f8TTbueSAR7vIXyfzamQ4cTC9/V+IRUyrjqKbkWy40azOJN9ChOhWhUnrpdkTOTt2r7hcsGdi2dj8U/c8mLCczpYvapvcl3gCaKdFQUR8+lKLwvbaoKYPO4wDoCRBa5lNDg66lmbOg9QV2vK2PyTmP/0zRP361PzUIPyxNUANdAYjaQqvgK67bp5ct9b9/cD58ibgYtiDoZVdcqw/ZVEGSKm11x41F9tZNwD0JVruIuHmMHYPD9U7EMkEFI3D2wUlIuCvOPFudmypXA/AhHScpTyAe+iJ7SRR3scrJQfSuWHPUJWRJHW5XtBy4KUMadvpuu/la6/ky6IiuMpE+5mu8u3t5RJd7lgjr/d8d9+raH4YFddV8ek5kfNSlnP/rLYVDu3tuU04omMxBJYOUxG48tTqHfWZm2h1+L/T9EVQ/auWhhlVbIlv/jYTSO8xFSXolHeZcgY7rdCIBu1G3t7J2tGV9esXcMqQ6Fv5ChFMgtdUU9m+UHX/gV10gpZ'
OWNER_LABEL='portal-ppt-network-owner'
GUARD_CONTAINER='network-link-guard'
RECEIPT_VOLUME='portal-ppt-link-guard'
RECEIPT_PATH='/portal-ppt-link-guard'
POD_UID_ENV='PPT_GUARD_POD_UID'
GUARD_IMAGE=dict(manifest_digest='sha256:9b3a6d80d20b73585c6642213077236bb9948f35d0d5daa262e78d120422eb9e',
                 config_digest='sha256:5b43bc9cd5f51029b87aaf0b212c064124e29fb814c5543b7de7d864812f2902',
                 digest_reference='deerflow.local/deer-flow-gateway@sha256:9b3a6d80d20b73585c6642213077236bb9948f35d0d5daa262e78d120422eb9e')
GUARD_SECURITY=dict(runAsUser=0,runAsGroup=0,runAsNonRoot=False,readOnlyRootFilesystem=True,
                    allowPrivilegeEscalation=False,capabilities=dict(drop=['ALL'],add=['NET_ADMIN']),
                    seccompProfile=dict(type='RuntimeDefault'))


def require(ok,code):
    if not ok: raise ValueError(code)


def unpack(value,sha):
    decoder=zlib.decompressobj()
    raw=decoder.decompress(base64.b64decode(value,validate=True),131073)
    require(len(raw)<=131072 and decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data
            and hashlib.sha256(raw).hexdigest()==sha,'guard_asset_digest')
    return raw.decode()


def guard_source(): return unpack(GUARD_Z64,GUARD_CODE_SHA)
def wrapper_source(): return unpack(WRAPPER_Z64,WRAPPER_SHA)


def identity(owner,namespace):
    require(isinstance(owner,str) and re.fullmatch('[a-f0-9]{32}',owner),'guard_owner')
    require(namespace=='portal-ppt-'+owner,'guard_namespace')


def pod_scope(pod,owner,namespace):
    identity(owner,namespace)
    require(pod['kind']=='Pod' and pod['metadata']['namespace']==namespace
            and pod['metadata']['labels'].get(OWNER_LABEL)==owner,'guard_pod_owner')
    spec=pod['spec']
    require(all(spec.get(name,False) is False for name in ('hostNetwork','hostPID','hostIPC'))
            and spec.get('automountServiceAccountToken') is False,'guard_host_or_token_scope')
    require(not spec.get('ephemeralContainers') and not spec.get('shareProcessNamespace',False),'guard_process_scope')
    require(all(set(volume)-{'name'} in ({'emptyDir'},{'configMap'}) for volume in spec.get('volumes',[])),'guard_volume_scope')


def guard_argv(owner,pod_uid,write=False):
    argv=['/usr/bin/env','-i','PATH=/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin',
          '/app/backend/.venv/bin/python','-I','-S','-B','-u','-c',guard_source(),
          '--owner',owner,'--pod-uid',pod_uid,'--code-sha',GUARD_CODE_SHA]
    if write: argv += ['--receipt-directory',RECEIPT_PATH]
    return argv


def proof_guard_argv(owner,pod_uid):
    identity(owner,'portal-ppt-'+owner)
    require(re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',pod_uid),'proof_guard_pod_uid')
    check='test "${PROOF_POD_UID:-}" = "$1" || exit 92; shift; exec "$@"'
    return ['/bin/sh','-eu','-c',check,'portal-proof-live-uid',pod_uid]+guard_argv(owner,pod_uid)


def decorate_proof_subject(pod,owner,namespace):
    pod=copy.deepcopy(pod); pod_scope(pod,owner,namespace)
    require(pod['metadata']['name']=='network-subject' and not pod['spec'].get('initContainers')
            and len(pod['spec']['containers'])==1 and pod['spec']['containers'][0]['name']=='proof','proof_subject_shape')
    container=pod['spec']['containers'][0]
    require(container['image']==GUARD_IMAGE['digest_reference'] and container['imagePullPolicy']=='Never','proof_subject_image')
    container['securityContext']=copy.deepcopy(GUARD_SECURITY)
    return pod


def guard_container(owner):
    return dict(name=GUARD_CONTAINER,image=GUARD_IMAGE['digest_reference'],imagePullPolicy='Never',
                command=guard_argv(owner,'$(PPT_GUARD_POD_UID)',True),
                env=[dict(name=POD_UID_ENV,valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid')))],
                securityContext=copy.deepcopy(GUARD_SECURITY),
                resources=dict(requests=dict(cpu='100m',memory='64Mi'),limits=dict(cpu='100m',memory='64Mi')),
                volumeMounts=[dict(name=RECEIPT_VOLUME,mountPath=RECEIPT_PATH)])


def wrap_container(container,owner):
    container=copy.deepcopy(container)
    security=container.get('securityContext',{})
    require(security.get('allowPrivilegeEscalation') is False and security.get('readOnlyRootFilesystem') is True
            and security.get('capabilities',{}).get('drop')==['ALL']
            and not security.get('capabilities',{}).get('add') and not security.get('privileged',False), 'business_caps')
    require(not any(e['name']==POD_UID_ENV for e in container.get('env',[])), 'guard_env_collision')
    require(not any(v['name']==RECEIPT_VOLUME or v['mountPath']==RECEIPT_PATH for v in container.get('volumeMounts',[])), 'guard_mount_collision')
    argv=container.get('command',[])+container.get('args',[])
    require(container.get('command') and argv and all(isinstance(x,str) and '\x00' not in x for x in argv),'business_explicit_argv')
    container['command']=['/bin/sh']
    container['args']=['-eu','-c',wrapper_source(),'portal-ppt-link-preexec',owner,GUARD_CODE_SHA]+argv
    container.setdefault('env',[]).append(dict(name=POD_UID_ENV,valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid'))))
    container.setdefault('volumeMounts',[]).append(dict(name=RECEIPT_VOLUME,mountPath=RECEIPT_PATH,readOnly=True))
    return container


def decorate_business_pod(pod,owner,namespace):
    pod=copy.deepcopy(pod); pod_scope(pod,owner,namespace)
    spec=pod['spec']
    require(not pod['metadata']['name'].startswith('network-'),'business_not_network_proof')
    require(not any(v['name']==RECEIPT_VOLUME for v in spec.get('volumes',[])),'guard_volume_collision')
    require(not any(c['name']==GUARD_CONTAINER for c in spec.get('initContainers',[])+spec['containers']),'guard_container_collision')
    spec['initContainers']=[guard_container(owner)]+[wrap_container(c,owner) for c in spec.get('initContainers',[])]
    spec['containers']=[wrap_container(c,owner) for c in spec['containers']]
    spec.setdefault('volumes',[]).append(dict(name=RECEIPT_VOLUME,emptyDir=dict(medium='Memory',sizeLimit='1Mi')))
    return pod


def validate_pod_guard_shape(pod,owner,namespace):
    pod_scope(pod,owner,namespace); spec=pod['spec']
    require(spec.get('initContainers') and spec['initContainers'][0]==guard_container(owner),'guard_first_ordinary_init')
    require(sum(c['name']==GUARD_CONTAINER for c in spec['initContainers']+spec['containers'])==1,'guard_unique_init')
    volumes=[v for v in spec['volumes'] if v['name']==RECEIPT_VOLUME]
    require(volumes==[dict(name=RECEIPT_VOLUME,emptyDir=dict(medium='Memory',sizeLimit='1Mi'))],'guard_tmpfs_volume')
    for container in spec['initContainers'][1:]+spec['containers']:
        prefix=['-eu','-c',wrapper_source(),'portal-ppt-link-preexec',owner,GUARD_CODE_SHA]
        require(container.get('command')==['/bin/sh'] and container.get('args',[])[:6]==prefix
                and len(container['args'])>6,'business_preexec_wrapper')
        original=copy.deepcopy(container)
        original['command']=original.pop('args')[6:]
        added=dict(name=POD_UID_ENV,valueFrom=dict(fieldRef=dict(fieldPath='metadata.uid')))
        require(original['env'][-1]==added and sum(e['name']==POD_UID_ENV for e in original['env'])==1,'guard_uid_binding')
        original['env']=original['env'][:-1]
        mount=dict(name=RECEIPT_VOLUME,mountPath=RECEIPT_PATH,readOnly=True)
        require(original['volumeMounts'][-1]==mount,'guard_receipt_readonly')
        original['volumeMounts']=original['volumeMounts'][:-1]
        require(wrap_container(original,owner)==container,'business_guard_shape')
    return dict(status='EXACT_GUARD_AND_PREEXEC_SHAPE',ordinary_init_first=True,receipt_all_business_readonly=True)


def validate_guard_receipt(raw,owner,pod_uid,observed_image):
    require(isinstance(raw,bytes) and len(raw)<=65536,'guard_receipt_bound')
    def pairs(values):
        result={}
        for key,value in values:
            require(key not in result,'guard_duplicate_json');result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs)
    require(value.get('schema')=='portal-owned-pod-link-guard.v1' and value.get('status')=='OWNED_NETNS_NON_LOOPBACK_DOWN'
            and value.get('owner')==owner and value.get('pod_uid')==pod_uid and value.get('code_sha256')==GUARD_CODE_SHA,'guard_receipt_identity')
    actual=observed_image.rsplit('@',1)[-1].split('://')[-1]
    require(actual in (GUARD_IMAGE['manifest_digest'],GUARD_IMAGE['config_digest']),'guard_actual_image')
    require(value.get('image')==dict(manifest_digest=GUARD_IMAGE['manifest_digest'],config_digest=GUARD_IMAGE['config_digest']),'guard_receipt_image')
    require(value.get('scope')=='THIS_OWNED_POD_NETWORK_NAMESPACE_ONLY' and value.get('capabilities_revoked_by_guard') is False
            and value.get('loopback_up') is True and value.get('non_loopback_down') is True,'guard_receipt_scope')
    require(value.get('guard_privileges')==dict(CapEff=4096,CapPrm=4096,CapBnd=4096,CapAmb=0,CapInh=0,NoNewPrivs=1),'guard_receipt_privileges')
    before,after=value['before'],value['after']
    require(before['netns']==after['netns'] and set(before['netns'])=={'device','inode','link'},'guard_receipt_netns')
    ns=after['netns']
    require(type(ns['device']) is int and ns['device']>=0 and type(ns['inode']) is int and ns['inode']>0
            and ns['link']=='net:[%d]'%ns['inode'],'guard_receipt_netns_shape')
    for sample in (before,after):
        rows=sample['interfaces']
        require(isinstance(rows,list) and 1<=len(rows)<=64 and rows==sorted(rows,key=lambda row:row['name']),'guard_receipt_interfaces')
        require(all(set(row)=={'index','name','flags'} and type(row['index']) is int and row['index']>0
                    and isinstance(row['name'],str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',row['name'])
                    and type(row['flags']) is int and 0<=row['flags']<=65535 for row in rows),'guard_receipt_interface_shape')
        require(len({row['index'] for row in rows})==len(rows) and len({row['name'] for row in rows})==len(rows),'guard_receipt_duplicates')
        require(sum(row['name']=='lo' for row in rows)==1 and all(bool(row['flags']&8)==(row['name']=='lo') for row in rows),'guard_receipt_loopback')
        require(next(row for row in rows if row['name']=='lo')['flags']&1,'guard_receipt_lo_down')
    require([(r['index'],r['name']) for r in before['interfaces']]==[(r['index'],r['name']) for r in after['interfaces']],'guard_receipt_topology_changed')
    require(all(row['flags']&1==0 for row in after['interfaces'] if row['name']!='lo'),'guard_receipt_nonlo_up')
    return dict(status='OWNED_POD_LINK_GUARD_VERIFIED',owner=owner,pod_uid=pod_uid,code_sha256=GUARD_CODE_SHA,
                receipt_sha256=hashlib.sha256(raw).hexdigest(),netns=ns,interfaces=after['interfaces'],
                actual_image_id=observed_image,capabilities_revoked_by_guard=False,scope=value['scope'])
