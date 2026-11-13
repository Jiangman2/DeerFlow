"""Fixed-thread delivery inspection and non-secret Gateway mount metadata."""
THREAD='545097ca-bd47-4416-888e-b6579229c865'

def mount_metadata(pod):
    containers=[row for row in pod['spec'].get('containers',[]) if row.get('name')=='gateway']
    need(len(containers)==1,'GATEWAY_CONTAINER_REQUIRED')
    volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
    result=[]
    for mount in containers[0].get('volumeMounts',[]):
        target=mount.get('mountPath')
        need(isinstance(target,str) and target.startswith('/') and '\\' not in target and '\x00' not in target and str(PurePosixPath(target))==target and '..' not in PurePosixPath(target).parts,'MOUNT_METADATA_PATH')
        source=volumes.get(mount.get('name'),{})
        row={'mount_path':target,'read_only':mount.get('readOnly',False),'subpath':mount.get('subPath'),
            'variable_subpath':bool(mount.get('subPathExpr'))}
        if 'configMap' in source:
            item=source['configMap'];row.update(source_kind='ConfigMap',source_name=item.get('name'),
                items=[{'key':item.get('key'),'path':item.get('path')} for item in item.get('items',[])[:32]])
        elif 'persistentVolumeClaim' in source:row.update(source_kind='PersistentVolumeClaim',source_name=source['persistentVolumeClaim'].get('claimName'))
        elif 'secret' in source:row.update(source_kind='Secret',secret_name_and_values_exported=False)
        elif 'emptyDir' in source:row['source_kind']='EmptyDir'
        elif 'projected' in source:row['source_kind']='Projected'
        else:row['source_kind']='Other'
        result.append(row)
    need(len(result)<=64,'MOUNT_METADATA_COUNT_BOUND')
    return result

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.queries=child,None,None,0
    def command(self,args,body=None):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json']
        if self.pod:
            allowed=allowed or args==['get','pod',self.pod,'-o','json']
            allowed=allowed or args==['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child] and body==canonical({'thread_id':THREAD})
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        need(allowed and (args[0]=='exec' or body is None),'FIXED_READONLY_COMMAND_REQUIRED');need(self.queries<7,'QUERY_BOUND');self.queries+=1
        prefix=list(KUBE);timeout=25
        if args[0]=='exec':prefix[2]='--request-timeout=65s';timeout=70
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            if body is not None:incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=2097152 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(output.fileno()).st_size<=2097152 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                output.seek(0);return output.read(2097153).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    op=None
    out={'schema':'energy-delivery-budget-inspection.v1','status':'INCOMPLETE','thread_id':THREAD,'workload_modified':False,
        'database_modified':False,'persistent_files_modified':False,'credentials_exported':False,'secret_api_reads':0,'provider_requests':0,
        'work_messages_sent':0,'task_retry_requested':False,'recursion_limit_modified':False,'checkpoint_modified':False,'raw_messages_exported':False}
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
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID),gateway_mount_metadata=mount_metadata(pod))
        print(json.dumps({'phase':'READ_FIXED_THREAD_WEBHOOK_TIMELINE_AND_SCHEDULER_BUDGET'},sort_keys=True),flush=True)
        result=json.loads(op.command(['exec','-i',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child],canonical({'thread_id':THREAD})))
        out['observation']=result
        need(result.get('requested_thread_id')==THREAD,'FIXED_THREAD_DIAGNOSTIC_SCOPE_REQUIRED')
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']));dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        need(result.get('status') in ('DELIVERY_TIMELINE_OBSERVED_READONLY','NAMED_THREAD_RUN_NOT_FOUND'),'DELIVERY_TIMELINE_INCOMPLETE')
        out.update(status='DELIVERY_BUDGET_OBSERVED_NO_CHANGE',stable_gateway_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILURE_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),temporary_command_spools_cleaned=True)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
