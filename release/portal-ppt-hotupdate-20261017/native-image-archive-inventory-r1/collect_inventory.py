#!/usr/bin/env python3
"""Fixed datax-02 read-only inventory. This never authorizes image deletion."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import types
import zlib

GUARD_SHA = '81c069d566023c68301498742906cfc5f78207e70a8e0d03ef0d0632ff7d2e2d'
GUARD_Z64 = 'eNq1W2lz20aT/s5fgU2qFmSWggCeone5VbQE2SzLpJak7DhaF2oADETEIMDgkMzkzX/f7jlwkpTzYV31RsQcPT19Pt3A+/O/XWZJfGn74SUNn5X9Id1GYb/1008/rWiS7agShVTZx3RPYup2lZA+0/iChimFRyUl8RNNlWhPY5L6UagEkUOC4ACbFJek5PuF3tOAVMvf7aM4VWA50EmofLZJQkcD+bQlyTbwbfn4exKF8neUtLw42il7kuISRQzfw6NcEudUE/8pJEH+FDnfaJo/paT4fUjkz/Swp/lDlvluq7W8V6aKSmxvMnJdOtSN8cA2qD12iDfWyYCOxoZhG2rrfjVfrnApvTL0ge0ZBnGdwWQ4Ia5uj/TxFR07k/Fkoqut9fJhdW1aK/N+udpY6/cz3HZFnJE3JK53NbjqjXtuTx+ObZc4uqOP3f6YDoaDyWQ8MGBwOHZ1QvtXnm5MbM8jfV13r5AD8362Mm8kxcmAUGNAPHfojXv0qg8bPd2zgSF3NPSAmtPvGeMJHYxhqk8HV/aI2E7fGI70/shzhiWKb79szDXQ7F2NjdZmtnpnbqzr5Y0pj+rRsWHrPdftu72RPXaBlKvrV72BPRkaIKYrEJwzptTVqUsnk16vZ4wMQofAISXsqDJRedpgNB4Zrdnd3fJ6tpkvF/I03XUHrj3WdW/o9N0h0YkxsHt923D7w5E7hvtMRmPXM/oTh44mtgHPpE+GA7ihPrgauHix5e38Lmffcyn1RgP3yjEc6l7pvaHhjft9qoPUHOIOryZ6vzc2eo5hX5HBeEQ8zwHJjQ19qMOte6DS9Xvrdr54Z67ADhYbJAq0e8PRm0/zdw/GdzP72Pu8dn/11j33OtP/+OO339dPdmA80PmvN7//Zi7vP3+cq63r5WKzWt7BdjTptnr5TOJLMPRLNEgSXOz36UUILvZML8AXg4i4F3H/wonCNI6CC1X5D2V53xGy/IdEuA/nNJaLa/NHKMBO5uwXMQsTYj/8R9XWwMfGvNHQgdVO6/7h7d382vpgfino0tS5TJIt/s/aRklqUcdNiPWNHrR9ZsOm1XLZvAiMt1ou9cDX/8j8mLafSZDRruJELu28aSnwz/eUMEoVNsFH8F9M/IQqn3DQjOMobrMdgliyJe2YvAgCMU2zOJSxSINJUCab17b0u+s/0SRty600REIu56NKoI2319xst08kmwkIEa+YTDcxPvuhC4F02usCnSSLqUUSx/fZZAcF+b8gPY0fkZ/o4xY/PRw9ko1pIE2XPneV/MkPo9JT4v9JS4+71N9RK0xKQ44YkmfuY/+ZpNSKKXHbGIG7SuDv/FSc7kWxgtkhTOFGClug8ecuXiPN9gEtj4o/SafQDzsa1C0WBBil4cq5+oS6cVhbW/P1zXxVXHaHulRI6BY3gACuTKeKzkZze5CLlX9X9EjvgdxVeTV+sCUOclV+tgdUIPVokN9CODxmt+h0cWhprW6Wi7svyr/402J5u4R49bl4XryF8PWBE0rjQ3FZm4LEKKfssZt67pm7rsx3bb6letlirHLbYjgM/PAbThg5cflPiqVKV8qlRgcNRvmvKdd5SWaeH9C6xPDfi59u2d1cJjcPUIMa2yq4aRAl1HOntyRI8BIJZGOwqd2bCn/gaiAbPqMxk2MHgyUZTSkFcAD6Zs4f4z13En6JDgohHytLvTrDbFTYXqd+UWdLwqfKPYXXwfHcWPwQoU9xGTiI3Zipl3sSC3X5ebn3Co0fEm0fkBRY3oHcSZwmKMq2CnrMvqtc60AUojUFlbcZ8zrwyeatOIoaBpyTZhgIdyIHIdlBQNGSfeCnbVVTO4/6V6SlSsimyqMysXRHnK0POBD9WyUkdrajAShUJfEOfqCseBbhsVxeQnqRTwM3Qe8ux5EiK3SVgT4ZSX6qjKN++f6O8t8AQxhjfEAwbassdVxgoL4I/STdQ7xG3jgrmR34DgZdCyGe4AgegR0OPjUb8j9lMVbQNb6yWOiDNCgPxlK/TzSGO0CcK7I8pr2CkAjWteQBx3U0mTngFz+to8Vg5P6+rU5r2qqcNFVqEKMmbLxa1TaFXbq+k7aluqeFbruK1M+UW84l1yLkJ0jGJYGV2JiWfncb0eRbZtM4BKtMrBBuhvHI8gAWbANkjYLpCZ/vKnEWsuQC+MML/KctRJfUD4LcboW8ubcgQrH4XaWvoIuC9CVcukQnZUjG4rDEivva/qC2ikByJHkZvSvlF8XQe4OmsbFgAkJvwlK0PAkV6ivAGEAtUKH8SUPBsYVKrmkGgmwWYORn5Yb2kT1u4Hdb9fzv1AXu5W6GrSyOrSq7NYvHI4sHSZ6S2Dz9Th0ANrs9TCOXXaWUslScRU/NqaB9WFZVAmKyjrynSm0EZSHWVhA1ALbi8YhEQghrkPnFhfZxhFeBiwhaTJH8d9IuPE8YNJ/oym3CSABdwC6QHY4l7dqibl66Vg2I4Uo59ahyIurXqjT42pw7TAyIFNJt0gakXOyCi/KfbLKmc4YhBIW3DElYJHS2kJbcku4qAEGioR/DByew0I+BQXBVNOaGSzdkg1R8h+JlG1M+ev3RGSAvhVqHH5L/j+A/df4xr0VQ5hWCdeHKThrFhyN5mH7fwxxFOf8VRy+PKsZjDHrq1zcKDDCAin8hewmzeFT9HYF4rH79+yikkCQZKyPOLwQhUEgAGQaYycEDDuerC36ZiyZo803YEL1gMnz8mo8gf8hvV3KJhQKYh6Sr+SndJZA6qkCJnVGBqJzIP0CnXcgSMdCYli2sYY85oLETGj/TkkF7ELLoUxYQEAjnht3hUUWIgOHYPqQo5a4MwwL7NUhL4csjSrD2uFE1bai8tWRGo7IZMSajl5DGbIl6hBHQjkb2IE23zTIo1Nd0yu6Ev/Aq0rymXNzsitPy6bzC4g5TmUBPbJzY+MfcqbKP+ay0BkvKlqOLaUXefAy4fP0YWGo909j3fIf17qbqjXlrrrDrs1lat6vlb+bCypPc/Rdrtrixbh/u7qz3s/V7tdOpGWMN/TZRMDeQZiSrlbSnwfqZcGC5GWCTJw43jmB00OoPg3Q/TFDSljS8ek4RbpjH9IR4FDlqc2HVkHc1bGBGFst4/PhLrXRL4Iqy98YH/i7gHkkhDOzhb4BY5mCJJqwVxbUow2oHRBNNACQ6RICbaseOhsP+SKZlfu/z+6tsVgiUI2mZWIcDhEprUaKq0+s4kijmqUP9fXr8ykUKKidd1nJul6RSwCA5WTk+72rwRIxFT4N+KRZXmlGPat4MV1l9srwvWhOPWFKCumpLeAe5tIorXK0VZUfTaGWHxRvYDE1DsUExTvqJwmJueTVLfnLxjhwsm3VAUp8EpR3nzttDiUMtwlmsjNmC7cVyY63M/3kw12BlalmDHEf5Iaux6oUql/OjiouyRNCaL66XH+/vzI3JK9MiJVSW5SazWEJOwB5749xMYs8odOgxAxd2gV3QozZdsxtJhpksyxc1DU+ZcrvFK5Lp8r6rkCyNdvDowKGQakVWBGaj2H/CEGUxwrYfuhDVpI1TEjDDFk3RaJ+qHXDFU/CUr7c4EqpcAls/EJR4v4N+h9iUsMjEd3RK9TyOFAGHrRTcvAChYwIslWb3d7PFxef5ypQxQpZdyi+/KD0RbAISFr6ahYwsDtYlLc/rVIohWafzLQXohyehN462frhKKBccSKXLtSqDh8i5GLGq8aSwsmJFJaiACVR0W1qXG9GprI2MWEwApV25QLqleQ5GMPAW0+eIggCeAcwAA1N0n0/z5cPaup3/inGXv6x6u1xu1pvV7N7ClrwF5vxptjEtoWYL3Xy5mr+bL2Z3Fiqcx3X11KlVRSB4SUqSEVaXhSK3VdoBTkD8Xa46QFcAEhyZjSuANTedlxigsxXSlzZ3aNmvZzqtuqSkN0VHyImfctWOLLkduk+VW7iLyZyDvV04895BLdf1uWMJU8pfbzBEQdkGOEEmK3FTvAFYNExo7Kfvyb4Ca30mPiuyHVreCqK4JX6QYS+UYjJgl/zudHB3fUvBrlisYu1/B9F8/fDRtG5nUN/fqGVHkXzEUBFkQQDiciBAPc4ufiMXf+oXE+vrX0bX6Ol/q+JdzTm64g1PFrYrQIvFGdkvACl+QxkINcHPRRTyhCVfTU9LSQzFHWDXjvsygMQdmaqvvtPSng21Hrl5EpmWU9IxU0+iLEanpizFCq9tvP7tynUeVw5CTo76+Rui04RF7mYxf6o+LD4slp8XmPdW5sflxmQyvUNvNdfm6hNLwOW2lGCo3sI6cl49rlVh2VHngBKQWTUgKAtqpiDKUjFxhL5s28m+IEmSolsIjmthUxIIJc5ejgoRiJIngRvt/XzPkSNeovgb7yxlKVekIITIymY4xnFokshhrAyD2hhLfVhg8L4BVHIBTc8degZiTdFWc33UIRufPUJRrAf7hrInj1a57h8Wm/mdhUU9KCfX0vV78/oDKL/aiJPRtVSMx44iXxRVQikviGB5nO2xJ4HfVmQ7JAiwolN/c1OPdqWdVhhxIylVZ4ixgSBH2OyjDW09f7cxVx+7SvH8/uG+dpCY439wZ7fMY7n6Q79/VCuvXRAs1l/ElMvBbEeSb2090sfjTi2jdCshqNSczteJwCSirhPtdlGIvdxvpTU/K9dYpSYK+JWyg2SovGyR5jYKEO0p6RbiM0iXEyMJG5ApUpiB1iqn8a5ozTDrRrjzagnbENFTRmKXl88MTlco1lZr2R4xV/uUBQufOecCJ/3mjKUftW0EIrfz1UeG+jeWuQDrgXDX7DKcwrwIDBjifSUz593GIufkuZW97GizTxYulbb6Yx9L4FdGGv5n0GafFnSaSpFnMYVUcEm+9AQoOgeF8hsARH/3MFvdXNR6ARIlcTY6R4iBGT3BbNIWKVwkEp7JGznzNXiXizZP3TjekMbRGMZEU1kuvknL1SMMULhqpUTosi9e6pYiPAxXy/qSlXeqzPhognfL2Q0vRsvLZVnM1jcnbEHofrZeHy/nEbmX93EfQpQMUn38yntGo6I+w9c9kIlA0hBfU9HPq72lLrushC+Nq+T5iEOWKX86naVO4ZPXcl+t/8vSjl7G0m2T/YUbdZUP9GBHEJfmMrizjwZg4Zumq2DCYeCGO8txBJ1v873c2op3Wdj5wDCBGbiW2eS98bVxFAWyOOVdhqLP0ant+pFomf86HzHzZdUzcvSvcVh5+g4V32BAuLKxc2Jx3XLKwFcppD49pohTAPGV5vT/j6TK1lKxdWYwfKSx4WdlGQYHyMAgVj98jnirXEUZx7s8GyssVUEuPyisPflMFaxmtAY50Faphbp6WFzkpiO+ltP8hJXG7c6bo1I6+mKmFp95qBeN0rbKWBGhnfPbOUpBOGDuf6fPqYlRVi/83KIlpwA4nX2C02dv70xrubJmdytzdvPFMn+drzciAjY68o0b8iZTLSnVnSBPauft/5T0XkmT3EWkEPMe8T+XHtY3VSib7XYkPuS1KfcyKWApy3pGrdaV8quEfLzoAvFhgDspd1LnrO+ddryKvptd5rNUz/sqp3y2I32qCVO3ARFvWeIshaEjniSk3kgbx4LY6+9Fq73hsgXil29cJVtIM5Hnia8bAAKRZ/AaYgfHXkLm7LnRS8gKjVKkEqYqCfKZdsMmX7fHU6dUPPj9bHGzvL21yp68nt3mXQz2SZJsB1WoHxItSV0o/jU78zwac9dijP5nc9ILMoDQ1Suwz43K39EKjutf0oKFs+01OHn0laTv8bLqbJjIXwri0kpLOHZarRbQsFhbHT/CARlZYLF+aFmq/MQkhtIJhCf/3w7aLH4C1B+m92ym7dLEiX2mlalluZEjP8PhOzXi4ktevqWtXsiiIQrFt9oQgojDX9kmaQSBN4V7Y1yqfkRVosj+IM1Efl/HSvX1Adx3Z0J0aLN+W6f1fyxDuBM='
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
PLAN_SHA = 'b3f08cc0ca0f8cb75488ce37a26531071699f5eb5d9097687604851753d72aa9'
FAIL_SHA = '4a9f9824ca153f962a8d7191342811b03f8d6c9ef799b2f388ca51b5f1d9833a'
PRIOR_REPORT_SHA = '3b1c3d86982efd87a5322392276d49170ee50d547f103e61d4c2ef172718af16'
PROTECTED_HISTORY = [{'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'deerflow.local/deer-flow-frontend:20261016-shoptype-4b08bcc9-c55d72b6da1f'}, {'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'deerflow.local/deer-flow-gateway:20260811-enterprise-arm64-rc15-consolidated2'}, {'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15'}, {'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'deerflow.local/deer-flow-gateway:20260928-ppt-preview-profile-r4-runner-9473ecee03df4e43'}, {'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'deerflow.local/deer-flow-gateway:20261017-background-stream-953eb97e4aaf'}, {'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'deerflow.local/deer-flow-provisioner:20261005-prod-node-pool-r1'}, {'basis': 'RETAINED_NATIVE_PREFLIGHT_NOT_FRESH_USAGE', 'reference': 'm.daocloud.io/docker.io/library/busybox@sha256:9532d8c39891ca2ecde4d30d7710e01fb739c87a8b9299685c63704296b16028'}, {'basis': 'GATEWAY-EXPORT-RECONCILIATION.json', 'reference': 'deerflow.local/deer-flow-gateway:20261017-background-stream-953eb97e4aaf', 'manifest_digest': 'sha256:ef500864eb402d0b9a76556764dc0e2a9d3fe417f6fef89fbf4e3816ee0c1c84', 'config_digest': 'sha256:dff2b9d7323329733f14133b384030d6efeda948a86f082f055f1573ced224fd'}, {'basis': 'host-base-evidence-r1/REPORT.json', 'reference': 'deerflow.local/deer-flow-aio-sandbox:20260927-ppt-runtime-r2-agent-host-3959a039ee854dca', 'manifest_digest': 'sha256:126fabc46a5d4d3b9d50924722bf10e7a1003a4d1adbdf72ace8ae25e188f97e', 'config_digest': 'sha256:09ea44f66e1d47269b10e05f11f933f9f08897a1e5a55222a138f0a1b1acfaea'}, {'basis': 'TARGETED-BUILD-INPUT-REVIEW.json', 'reference': 'deerflow.local/deer-flow-gateway:20260928-ppt-preview-profile-r4-runner-9473ecee03df4e43', 'manifest_digest': 'sha256:eb23de08345bab65c78264f99ce33085c4ea3574ff8d6281b62418f644fc06cd', 'config_digest': 'sha256:bc378da76f8ba638256d7f70764dcd130a12cb44346a596ed7a17c4d61815cec'}, {'basis': 'OPERATOR-CRI-METADATA-20261017-r1.json', 'reference': 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1', 'config_digest': 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'}]
LIMIT = 8 * 1024 ** 2


def require(value, code):
    if not value:raise ValueError(code)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def number(value):
    require((type(value) is int or isinstance(value,str) and re.fullmatch('[0-9]{1,20}',value))
            and 0 <= int(value) <= 2**60, 'invalid_numeric_metadata')
    return int(value)


def image_id(value):
    require(isinstance(value,str) and re.fullmatch('(?:sha256:)?[a-f0-9]{64}',value), 'invalid_image_identity')
    return value if value.startswith('sha256:') else 'sha256:'+value


def object_id(value):
    require(isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value), 'invalid_container_identity')
    return value


def reference(value):
    require(isinstance(value,str) and len(value)<=512 and '://' not in value
            and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:/-]*(?:@sha256:[a-f0-9]{64})?',value), 'invalid_image_reference')
    return value


def references(value):
    if value is None:value=[]
    require(isinstance(value,list) and len(value)<=256, 'invalid_reference_list')
    return sorted(set(reference(item) for item in value))


def project_images(value):
    rows=value.get('images');require(isinstance(rows,list) and len(rows)<=4096,'invalid_cri_images')
    return [dict(id=image_id(row['id']),repoTags=references(row.get('repoTags',[])),
                 repoDigests=references(row.get('repoDigests',[])),size_bytes=number(row['size'])) for row in rows]


def project_containers(value):
    rows=value.get('containers');require(isinstance(rows,list) and len(rows)<=8192,'invalid_cri_containers')
    result=[]
    for row in rows:
        state=row['state'];require(state in ('CONTAINER_CREATED','CONTAINER_RUNNING','CONTAINER_EXITED','CONTAINER_UNKNOWN'),'invalid_container_state')
        image=row['image'];require(isinstance(image,dict),'invalid_container_image')
        result.append(dict(id=object_id(row['id']),image=reference(image['image']),imageRef=reference(row['imageRef']),
                           state=state,podSandboxId=object_id(row['podSandboxId'])))
    return result


def project_ctr(raw):
    lines=raw.decode('utf-8').splitlines()
    require(lines and lines[0].split()[:3]==['REF','TYPE','DIGEST'] and len(lines)<=8193,'invalid_ctr_header')
    result=[]
    for line in lines[1:]:
        words=line.split();require(len(words)>=3,'invalid_ctr_row')
        require(words[1] in ('application/vnd.oci.image.manifest.v1+json','application/vnd.oci.image.index.v1+json',
                            'application/vnd.docker.distribution.manifest.v2+json','application/vnd.docker.distribution.manifest.list.v2+json'), 'invalid_image_media_type')
        result.append(dict(reference=reference(words[0]),media_type=words[1],digest=image_id(words[2])))
    return result


def project_namespaces(raw):
    names=raw.decode('ascii').split()
    require(len(names)<=128 and len(names)==len(set(names))
            and all(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',name) for name in names),'invalid_namespace_projection')
    return sorted(names)


def filesystem(module,path):
    fd=module.B.open_anchored(Path(path))
    try:
        value,space=os.fstat(fd),os.fstatvfs(fd)
        require(stat.S_ISDIR(value.st_mode),'filesystem_not_directory')
        return dict(path=path,device=value.st_dev,uid=value.st_uid,gid=value.st_gid,mode=oct(stat.S_IMODE(value.st_mode)),
                    capacity_bytes=space.f_blocks*space.f_frsize,available_bytes=space.f_bavail*space.f_frsize,
                    available_inodes=space.f_favail,block_bytes=space.f_bsize,fragment_bytes=space.f_frsize,
                    read_only=bool(space.f_flag&os.ST_RDONLY))
    finally:os.close(fd)


def checkpoint_projection(value):
    result={}
    for name in ('operation','role','phase','status','error_code','phase_a','phase_b'):
        if name in value:
            item=value[name]
            require(isinstance(item,str) and re.fullmatch('[A-Za-z0-9_.-]{1,128}',item),'invalid_checkpoint_label')
            result[name]=item
    for name in ('automatic_retry','target_import_requested','image_import_may_be_partial','all_fixed_outer_blobs_present','all_refs_and_config_bound','all_six_complete'):
        if name in value:
            require(type(value[name]) is bool,'invalid_checkpoint_flag');result[name]=value[name]
    for name in ('archive_bytes','metadata_only_bytes','returncode','stdout_bytes','stderr_bytes'):
        if name in value:result[name]=number(value[name])
    if 'archive_sha256' in value:
        require(isinstance(value['archive_sha256'],str) and re.fullmatch('[a-f0-9]{64}',value['archive_sha256']),'invalid_checkpoint_sha')
        result['archive_sha256']=value['archive_sha256']
    return result


def checkpoints(module,profile):
    target=module.state_paths(OP)['target'];module.B.safe_dir(target)
    present=set(os.listdir(str(target)))
    names={'STARTED.json','PREPARED.json','RUN-REQUESTED.json','FAIL.json','REPORT.json',
           'PHASE-A-REQUESTED.json','PHASE-A-PASS.json','PHASE-B-REQUESTED.json','PHASE-B-PASS.json'}
    for row in profile['images']:
        names.add(row['role']+'.SEALED.json')
        for phase in ('PHASE-A','PHASE-B'):
            for state in ('REQUESTED','PASS'):names.add(row['role']+'.'+phase+'-'+state+'.json')
    required={'STARTED.json','PREPARED.json','RUN-REQUESTED.json','FAIL.json','PHASE-A-REQUESTED.json','PHASE-A-PASS.json'}
    for row in profile['images']:
        required.update(row['role']+suffix for suffix in ('.SEALED.json','.PHASE-A-REQUESTED.json','.PHASE-A-PASS.json'))
    result=dict(files=[],unexpected_names_sha256=sorted(sha(name.encode()) for name in present-names),
                any_phase_b_receipt=any('PHASE-B' in name for name in present),
                expected_state='PHASE_A_COMPLETE_PHASE_B_NOT_REQUESTED',state_unchanged=False)
    for name in sorted(names):
        row=dict(name=name,present=name in present)
        if row['present']:
            raw=module.private_bytes(target/name,1024**2)
            row.update(bytes=len(raw),sha256=sha(raw),safe=checkpoint_projection(module.B.parse(raw)))
        result['files'].append(row)
    hashes={row['name']:row.get('sha256') for row in result['files']}
    result['state_unchanged']=present==required and hashes.get('FAIL.json')==FAIL_SHA
    result['expected_checkpoint_file_count']=24
    result['actual_checkpoint_file_count']=len(present)
    result['phase_a_pass_receipt_present']='PHASE-A-PASS.json' in present
    sealed=Path('/opt')/module.state_paths(OP)['sealed_name']
    fd=module.B.open_anchored(sealed)
    try:
        value=os.fstat(fd)
        result['sealed_directory']=dict(display_path=str(sealed),device=value.st_dev,inode=value.st_ino,uid=value.st_uid,
                                        mode=oct(stat.S_IMODE(value.st_mode)),expected_device=64785,expected_inode=14340598,
                                        identity_matches=(value.st_dev,value.st_ino,value.st_uid,stat.S_IMODE(value.st_mode))==(64785,14340598,0,0o700))
    finally:os.close(fd)
    wire=module.private_bytes(module.state_paths(OP)['control']/'PLAN-WIRE.json',1024**2)
    result['plan_wire']=dict(bytes=len(wire),sha256=sha(wire),matches_accepted=len(wire)==122307 and sha(wire)==PLAN_SHA)
    if result['plan_wire']['matches_accepted']:
        module.validate_plan(profile,module.unwire_plan(module.B.parse(wire)))
        result['plan_wire']['fixed_image_structure_valid']=True
    return result


def safe_error(exc):
    if isinstance(exc,KeyboardInterrupt):return 'COLLECTION_INTERRUPTED'
    code=str(exc) if isinstance(exc,ValueError) else getattr(exc,'code','READ_ONLY_STEP_FAILED')
    return code if isinstance(code,str) and re.fullmatch('[A-Za-z0-9_.-]{1,128}',code) else 'READ_ONLY_STEP_FAILED'


def command_step(module,report,name,argv,parser,json_input=False):
    step=dict(name=name,status='NOT_COLLECTED');report['steps'].append(step)
    try:
        rc,raw=module.B.run(argv,'READ_ONLY_'+name,timeout=30,limit=LIMIT,allow_failure=True)
        step.update(returncode=rc,stdout_bytes=len(raw))
        require(rc==0,'COMMAND_NONZERO_EXIT')
        report[name]=parser(module.B.parse(raw) if json_input else raw)
        step['status']='COMPLETE'
    except Exception as exc:step['error_code']=safe_error(exc)


def report_bytes(report):
    raw=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
    if len(raw)>LIMIT:
        report['status']='PARTIAL_REVIEW_REQUIRED'
        report['output_limit_observation']=dict(projected_bytes=len(raw),projected_sha256=sha(raw),limit=LIMIT)
        for name in ('cri_images','cri_containers','containerd_images','containerd_namespaces'):
            if name in report:report[name]=dict(status='OMITTED_REPORT_SIZE_LIMIT',row_count=len(report[name]))
        raw=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
    require(len(raw)<=LIMIT,'safe_report_size_limit')
    return raw


def run():
    module=None;report=dict(schema='portal-native-image-archive-inventory.v1',operation=OP,status='INCOMPLETE',
        collected_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        scope='LOCAL_DATAx02_CANDIDATE_INVENTORY_NOT_UNUSED_OR_DELETION_AUTHORIZATION',steps=[],
        prior_report_sha256=PRIOR_REPORT_SHA,expected_target_fail_sha256=FAIL_SHA,
        production_mutation=False,image_export=False,image_import=False,image_delete=False,image_prune=False,
        workload_mutation=False,database_access=False,model_access=False,ssh_scp=False,
        archive_payload_reads=False,raw_environment_exported=False,ready_for_deletion=False,ready_for_rollout=False,
        missing_proofs=['Fresh cluster Pod/controller desired images and explicit rollback retention are not collected.',
                        'Other containerd namespaces and lease/content sharing are not comprehensively reconciled.',
                        'Reported image sizes are not reclaimable disk bytes; aliases and shared layers can retain all data.'])
    rc=1
    try:
        def interrupted(signum,frame):raise KeyboardInterrupt()
        for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,interrupted)
        raw=zlib.decompress(base64.b64decode(GUARD_Z64,validate=True));require(sha(raw)==GUARD_SHA,'embedded_guard_changed')
        guard=types.ModuleType('fixed_guard_readonly_helpers');exec(compile(raw,'fixed_guard_readonly_helpers.py','exec'),guard.__dict__)
        report['host_identity']=guard.host_identity();module,profile=guard.load_target()
        module.B.DEADLINE=time.monotonic()+180
        report['protected_images']=dict(current_candidates=[{k:row[k] for k in ('role','tag','digest_reference','manifest_digest','config_digest')} for row in profile['images']],
                                        historical_inputs=PROTECTED_HISTORY,scope='PROTECTION_HINTS_NOT_EXHAUSTIVE_CURRENT_USAGE')
        for name,path in (('root_filesystem','/var/lib'),('opt_filesystem','/opt')):
            try:report[name]=filesystem(module,path)
            except Exception as exc:report['steps'].append(dict(name=name,status='NOT_COLLECTED',error_code=safe_error(exc)))
        try:report['checkpoint']=checkpoints(module,profile)
        except Exception as exc:report['steps'].append(dict(name='checkpoint',status='NOT_COLLECTED',error_code=safe_error(exc)))
        command_step(module,report,'cri_images',module.B.CRI+['images','-o','json'],project_images,True)
        command_step(module,report,'cri_containers',module.B.CRI+['ps','-a','-o','json'],project_containers,True)
        command_step(module,report,'containerd_images',module.B.CTR+['images','ls'],project_ctr)
        command_step(module,report,'containerd_namespaces',module.B.CTR+['namespaces','list','--quiet'],project_namespaces)
        report['status']='COMPLETE_REVIEW_REQUIRED' if all(s['status']=='COMPLETE' for s in report['steps']) else 'PARTIAL_REVIEW_REQUIRED'
        rc=0 if report['status']=='COMPLETE_REVIEW_REQUIRED' else 1
    except (Exception,KeyboardInterrupt) as exc:report['error_code']=safe_error(exc)
    raw=report_bytes(report)
    if report['status']!='COMPLETE_REVIEW_REQUIRED':rc=1
    summary=dict(status=report['status'],report_sha256=sha(raw),report_bytes=len(raw),exitcode=rc,ready_for_deletion=False)
    try:
        require(module is not None,'handoff_unavailable')
        summary['download_report']=module.handoff_report(raw)
    except Exception:
        summary['download_report_status']='UNAVAILABLE_SAFE_REPORT_PRINTED'
        sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    print(json.dumps(summary,sort_keys=True),flush=True)
    return rc


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--collect',action='store_true',required=True);parser.parse_args()
    raise SystemExit(run())
