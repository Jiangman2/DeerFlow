"""Host metadata projection and transport for a review-only reclamation plan."""

SANDBOXES=['get','pods','-l','app.kubernetes.io/component=sandbox','-o','json']
USERDATA_CLAIM='deer-flow-userdata-rwx-pvc'
FIXED_TASK_IDENTIFIER='887ea9f6-1347-4f0f-ab6d-1295ae3ead49'

def posix_absolute(value):
    need(isinstance(value,str) and value.startswith('/') and '\\' not in value and '\x00' not in value and len(value)<=512,'MOUNT_PATH_SHAPE')
    parsed=PurePosixPath(value)
    need(str(parsed)==value and '..' not in parsed.parts,'MOUNT_PATH_SHAPE')
    return value

def subpath(value):
    need(isinstance(value,str) and len(value)<=512 and '\\' not in value and '\x00' not in value,'SUBPATH_SHAPE')
    if value=='':return value
    parsed=PurePosixPath(value)
    need(not parsed.is_absolute() and str(parsed)==value and '..' not in parsed.parts and value!='.','SUBPATH_SHAPE')
    return value

def gateway_storage(pod):
    main=[row for row in pod['spec'].get('containers',[]) if row.get('name')=='gateway']
    need(len(main)==1,'GATEWAY_CONTAINER_REQUIRED')
    volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
    mounts=[];shadow=[]
    for row in main[0].get('volumeMounts',[]):
        path=posix_absolute(row.get('mountPath'))
        claim=volumes.get(row.get('name'),{}).get('persistentVolumeClaim',{}).get('claimName')
        if claim==USERDATA_CLAIM:
            need(not row.get('subPathExpr'),'VARIABLE_GATEWAY_STORAGE_REFUSED')
            mounts.append({'mount_path':path,'claim':claim,'subpath':subpath(row.get('subPath',''))})
        else:shadow.append(path)
    need(0<len(mounts)<=4 and len(shadow)<=32,'GATEWAY_USERDATA_MOUNT_REQUIRED')
    return mounts,shadow

def resource_column(containers,key):
    values=[]
    for row in containers:
        value=row.get('resources',{}).get('requests',{}).get(key,'<none>')
        need(isinstance(value,str) and len(value)<=64 and (value=='<none>' or re.fullmatch(r'[0-9][0-9.eE+-]*(?:[A-Za-z]{1,3})?',value)),'RESOURCE_REQUEST_SHAPE')
        values.append(value)
    return ','.join(values)

def project_sandboxes(value):
    items=value.get('items')
    need(isinstance(items,list) and len(items)<=256,'SANDBOX_COUNT_BOUND')
    rows=[];names=set()
    for pod in items:
        meta=pod.get('metadata',{});spec=pod.get('spec',{});status=pod.get('status',{})
        name=meta.get('name');uid=meta.get('uid')
        need(meta.get('namespace')=='deer-flow' and re.fullmatch(r'sandbox-[0-9a-f]{8}',name or '') and re.fullmatch(r'[0-9a-f-]{36}',uid or ''),'SANDBOX_RESOURCE_IDENTITY')
        need(meta.get('labels',{}).get('app.kubernetes.io/component')=='sandbox' and name not in names,'SANDBOX_COMPONENT_REQUIRED')
        names.add(name)
        containers=spec.get('containers',[]);need(isinstance(containers,list) and 0<len(containers)<=8,'SANDBOX_CONTAINER_BOUND')
        main=[row for row in containers if row.get('name')=='sandbox']
        candidates=[]
        if len(main)==1:
            candidates=[row for row in main[0].get('volumeMounts',[]) if row.get('mountPath')=='/mnt/user-data']
        volumes={row['name']:row for row in spec.get('volumes',[])}
        storage=candidates[0] if len(candidates)==1 and not candidates[0].get('subPathExpr') else {}
        claim=volumes.get(storage.get('name'),{}).get('persistentVolumeClaim',{}).get('claimName')
        raw_subpath=storage.get('subPath')
        if claim!=USERDATA_CLAIM:claim=None;raw_subpath=None
        if raw_subpath is not None:raw_subpath=subpath(raw_subpath)
        thread=meta.get('annotations',{}).get('deerflow.io/thread-id')
        need(thread is None or isinstance(thread,str) and len(thread)<=128,'THREAD_ANNOTATION_BOUND')
        user=meta.get('annotations',{}).get('deerflow.io/user-id')
        need(user is None or isinstance(user,str) and len(user)<=128,'USER_ANNOTATION_BOUND')
        node=spec.get('nodeName');need(node is None or isinstance(node,str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',node),'NODE_NAME_SHAPE')
        phase=status.get('phase');need(phase is None or phase in ('Pending','Running','Succeeded','Failed','Unknown'),'POD_PHASE_SHAPE')
        states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
        rows.append({'name':name,'uid':uid,'node':node,'phase':phase,'deleting':bool(meta.get('deletionTimestamp')),
            'sandbox_ready':len(states)==1 and states[0].get('ready') is True,
            'pod_ready':any(row.get('type')=='Ready' and row.get('status')=='True' for row in status.get('conditions',[])),
            'thread_annotation':thread,'user_annotation':user,'spec_sha256':digest(canonical(spec)),
            'userdata_subpath':raw_subpath,'userdata_mount_name':storage.get('name'),
            'userdata_claim':claim,'cpu_requests':resource_column(containers,'cpu'),'memory_requests':resource_column(containers,'memory')})
    return sorted(rows,key=lambda row:row['name'])

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.queries,self.payload=child,None,None,0,None
    def command(self,args,body=None):
        allowed=args==LIST or args==SANDBOXES or args==['get','deployment','gateway','-o','json']
        if self.pod:
            allowed=allowed or args==['get','pod',self.pod,'-o','json']
            allowed=allowed or args==['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child] and self.payload is not None and body==canonical(self.payload)
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        need(allowed and (args[0]=='exec' or body is None),'FIXED_PLAN_READ_REQUIRED');need(self.queries<9,'QUERY_BOUND');self.queries+=1
        prefix=list(KUBE);timeout=25
        if args[0]=='exec':prefix[2]='--request-timeout=65s';timeout=70
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            if body is not None:incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=8388608 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(output.fileno()).st_size<=8388608 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                output.seek(0);return output.read(8388609).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-scheduled-run-and-capacity',required=True,action='store_true');parser.parse_args()
    op=None
    out={'schema':'energy-scheduled-run-capacity-inspection.v1','status':'INCOMPLETE','workload_modified':False,'persistent_files_modified':False,
        'database_modified':False,'secret_api_reads':0,'credentials_exported':False,'provider_requests':0,'sandboxes_created':0,'sandboxes_deleted':0,
        'fixed_task_identifier':FIXED_TASK_IDENTIFIER,'task_retry_requested':False,'recursion_limit_modified':False,
        'cleanup_requested':False,'plan_only':True,'no_deletion_authorization':True,'unleased_nonrun_activity_not_excluded':True,
        'existing_lock_files_opened_without_create_or_truncate':True,'brief_nonblocking_exclusive_lock_probes_requested':True}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(assets());dep=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(dep['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_DEPLOYMENT_CHANGED')
        dep_identity={'uid':GATEWAY_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':digest(canonical(dep['spec']))}
        chosen=candidates(op.command(LIST))[0];op.pod=chosen[0]
        pod=json.loads(op.command(['get','pod',op.pod,'-o','json']));before=identity(pod,chosen[1])
        owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]+',owners[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        op.rs=owners[0]['name'];rs=json.loads(op.command(['get','replicaset',op.rs,'-o','json']))
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID))
        print(json.dumps({'phase':'READ_FIXED_FAILED_RUN_AND_REVIEW_SANDBOX_CAPACITY'},sort_keys=True),flush=True)
        mounts,shadow=gateway_storage(pod)
        rows=project_sandboxes(json.loads(op.command(SANDBOXES)))
        op.payload={'pods':rows,'userdata_mounts':mounts,'shadow_mount_paths':shadow}
        need(len(canonical(op.payload))<=524288,'CHILD_INPUT_BOUND')
        result=json.loads(op.command(['exec','-i',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child],canonical(op.payload)))
        out['observation']=result
        diagnostic=result.get('fixed_run_diagnostic')
        need(isinstance(diagnostic,dict) and diagnostic.get('requested_identifier')==FIXED_TASK_IDENTIFIER,'FIXED_TASK_DIAGNOSTIC_SCOPE_REQUIRED')
        out['fixed_run_diagnostic']=result.pop('fixed_run_diagnostic')
        need(result.get('status')=='SANDBOX_RECLAIM_REVIEW_PLAN_OBSERVED_NO_CHANGE','RECLAIM_PLAN_CHILD_INCOMPLETE')
        final_rows=project_sandboxes(json.loads(op.command(SANDBOXES)))
        first={row['name']:row for row in rows};last={row['name']:row for row in final_rows}
        reviewed=result.get('review_candidates');need(isinstance(reviewed,list) and len(reviewed)<=len(rows),'REVIEW_CANDIDATE_BOUND')
        stable=[];changed=0;seen=set()
        for row in reviewed:
            name=row.get('pod');need(name in first and name not in seen and row.get('uid')==first[name]['uid'],'REVIEW_CANDIDATE_IDENTITY')
            seen.add(name)
            if last.get(name)==first[name]:stable.append(row)
            else:changed+=1
        result['candidate_count_before_pod_recheck']=result.get('candidate_count',len(reviewed))
        result['review_candidates']=stable
        result['candidate_count']=len(stable)
        result['pod_results_are_child_snapshot_before_kubernetes_readback']=True
        details=result.pop('pod_results',[])
        need(isinstance(details,list) and len(details)<=256,'POD_SUMMARY_BOUND')
        exclusions={}
        for row in details:
            for reason in row.get('exclusion_reasons',[]):exclusions[reason]=exclusions.get(reason,0)+1
        result['exclusion_reason_counts_not_mutually_exclusive']=exclusions
        result['excluded_pod_samples']=[row for row in details if row.get('exclusion_reasons')][:8]
        result['excluded_pod_samples_are_not_complete_inventory']=True
        pool={'ecs-zdz-pro-00%02d'%number for number in range(11,18)}
        out['sandbox_counts_final']={'total':len(final_rows),'bound_to_current_pool':sum(row['node'] in pool for row in final_rows),
            'unassigned_pending':sum(not row['node'] and row['phase']=='Pending' for row in final_rows),
            'deleting':sum(row['deleting'] for row in final_rows),'fully_ready':sum(row['pod_ready'] and row['sandbox_ready'] for row in final_rows)}
        out.update(review_candidate_count=len(stable),candidates_removed_due_to_changed_projection=changed,
            review_plan_sha256=digest(canonical(stable)),first_sandbox_count=len(rows),final_sandbox_count=len(final_rows),candidate_projection_stable_readback=True)
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']));dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        need(out['fixed_run_diagnostic'].get('status') not in (None,'','INCOMPLETE'),'FIXED_RUN_DIAGNOSTIC_INCOMPLETE')
        out.update(status='SCHEDULED_RUN_AND_CAPACITY_OBSERVED_NO_CHANGE',stable_gateway_readback=True,
            candidate_verdict='NO_ACTIVE_RUN_AND_LOCKS_AVAILABLE_REQUIRES_REVIEW',
            nonrun_upload_conversion_and_preview_activity_not_proven_idle=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'PLAN_INSPECTION_FAILURE_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),temporary_command_spools_cleaned=True)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
