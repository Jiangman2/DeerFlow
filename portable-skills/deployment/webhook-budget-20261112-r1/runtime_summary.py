"""Bounded runtime/config metadata; receives an already loaded AppConfig.

Do not import application/model/provider modules or load the configuration here.
Only the four named source files are hashed; config file contents are not read.
"""
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import stat
import sys

SOURCE_PATHS={
    'lead_agent':'/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py',
    'services':'/app/backend/app/gateway/services.py',
    'scheduler_config':'/app/backend/packages/harness/deerflow/config/scheduler_config.py',
    'app_config':'/app/backend/packages/harness/deerflow/config/app_config.py',
}
SOURCE_LIMIT=1048576
TOTAL_SOURCE_LIMIT=4194304
PACKAGES=('langchain','langgraph','langchain-core')
SWITCHES=('summarization','token_usage','loop_detection','token_budget','safety_finish_reason','subagents','memory')
APP_CONFIG_MODULE='deerflow.config.app_config'
DEFAULT_CONFIG_PATH='/app/backend/config.yaml'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _attribute(value,name):
    try:
        return getattr(value,name,None)
    except Exception:
        return None


def _number(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and 1<=value<=10000000 else None


def _boolean(value):
    return value if isinstance(value,bool) else None


def _signature(value):
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def _source_hash(path,remaining):
    target=Path(path)
    result={'present':False,'source_text_exported':False}
    descriptor=None
    try:
        for part in (target,)+tuple(target.parents):
            if part.is_symlink():
                result['error_code']='SOURCE_LINK_REFUSED'
                return result,0
        descriptor=os.open(str(target),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        before=os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=min(SOURCE_LIMIT,remaining):
            result['error_code']='FIXED_SOURCE_BOUND'
            return result,0
        with os.fdopen(descriptor,'rb',closefd=False) as stream:
            raw=stream.read(before.st_size)
        after=os.fstat(descriptor)
        if len(raw)!=before.st_size or _signature(before)!=_signature(after) or _signature(after)!=_signature(target.stat()):
            result['error_code']='FIXED_SOURCE_CHANGED'
            return result,len(raw)
        result.update(present=True,bytes=len(raw),sha256=_sha(raw),stable_readback=True)
        return result,len(raw)
    except FileNotFoundError:
        result['error_code']='FIXED_SOURCE_NOT_PRESENT'
    except Exception as exc:
        result.update(error_class=type(exc).__name__,error_code='FIXED_SOURCE_UNAVAILABLE_REDACTED')
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return result,0


def _cached_config_metadata(config):
    module=sys.modules.get(APP_CONFIG_MODULE)
    result={'module_already_loaded':module is not None,'configuration_body_read':False,
            'cached_path_is_not_proof_of_live_gateway_process_state':True}
    if module is None:
        return result
    result['supplied_config_is_module_cached_instance']=_attribute(module,'_app_config') is config
    result['custom_config_cache']=_boolean(_attribute(module,'_app_config_is_custom'))
    cached=_attribute(module,'_app_config_path')
    if not isinstance(cached,(str,Path)):
        result['cached_path_present']=False
        return result
    raw_path=str(cached)
    if not raw_path or len(raw_path)>4096 or '\x00' in raw_path:
        result.update(cached_path_present=True,error_code='CACHED_CONFIG_PATH_SHAPE')
        return result
    result.update(cached_path_present=True,path_sha256=_sha(raw_path.encode('utf-8')),
                  path_is_absolute=Path(raw_path).is_absolute(),matches_default_config_path=raw_path==DEFAULT_CONFIG_PATH,
                  known_path=DEFAULT_CONFIG_PATH if raw_path==DEFAULT_CONFIG_PATH else None)
    try:
        state=Path(raw_path).lstat()
        result['file_metadata']={'present':True,'regular':stat.S_ISREG(state.st_mode),'symlink':stat.S_ISLNK(state.st_mode),
                                 'bytes':state.st_size,'mtime_ns':state.st_mtime_ns,'inode':state.st_ino}
    except FileNotFoundError:
        result['file_metadata']={'present':False}
    except Exception as exc:
        result['file_metadata']={'error_class':type(exc).__name__}
    signature=_attribute(module,'_app_config_signature')
    if isinstance(signature,tuple) and len(signature)==3:
        digest=signature[2]
        if isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest):
            result['cached_content_sha256']=digest
    return result


def observe_runtime(config):
    """Return numeric/bool metadata from the caller's existing config instance."""
    result={'schema':'energy-runtime-budget-summary.v1','status':'RUNTIME_CONFIG_METADATA_OBSERVED_NO_CHANGE',
            'configuration_loaded_again':False,'application_modules_imported':False,'provider_instantiated':False,
            'provider_requests':0,'files_modified':False,'credentials_exported':False,
            'missing_attributes_remain_unknown_no_defaults_inferred':True,
            'configuration_switches_are_not_actual_run_middleware_proof':True,
            'source_hashes_are_on_disk_not_loaded_code_proof':True,
            'source_read_limit_bytes':TOTAL_SOURCE_LIMIT}
    scheduler=_attribute(config,'scheduler')
    result['budgets']={'scheduler_recursion_limit':_number(_attribute(scheduler,'recursion_limit')),
                       'max_recursion_limit':_number(_attribute(config,'max_recursion_limit'))}
    result['feature_switches']={name:_boolean(_attribute(_attribute(config,name),'enabled')) for name in SWITCHES}
    result['subagent_run_mode_not_inferred_from_global_config']=True
    versions={}
    for package in PACKAGES:
        try:
            version=importlib.metadata.version(package)
            versions[package]=version if isinstance(version,str) and re.fullmatch('[0-9A-Za-z][0-9A-Za-z.+!_-]{0,127}',version) else None
        except Exception:
            versions[package]=None
    result['library_versions']=versions
    result['app_config_cache']=_cached_config_metadata(config)
    used=0;hashes={}
    for name,path in SOURCE_PATHS.items():
        hashes[name],count=_source_hash(path,TOTAL_SOURCE_LIMIT-used)
        used+=count
    result.update(source_hashes=hashes,source_bytes_read=used,all_fixed_sources_verified=all(row.get('present') is True for row in hashes.values()))
    return result
