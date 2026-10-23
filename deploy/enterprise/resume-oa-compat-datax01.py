"""datax-01-only continuation of a verified pre-import storage refusal."""
import hashlib
import json
import os
import stat

FAILED_PATH = '/var/lib/portal-runner-oa-schema-compatibility-report-mmfccu1f/REPORT.json'
FAILED_BYTES = 17517
FAILED_SHA = 'f80540b32a3f59549d31d6d96abbe8774297476425c72c38fc83205cbcc59210'
FAILED_OPERATION = '10ffc378f8de4920afcc9de26968fc71'
CLEAN_PATH = '/var/lib/portal-datax01-verified-cleanup-20261022-r1/REPORT.json'
CLEAN_BYTES = 6318
CLEAN_SHA = '44bed22e700b265adc7936cabe01819b5be9976b6a8120e25bacc50fb11ef4e8'
NEW_CONTROL = '/var/lib/portal-runner-oa-schema-compatibility-datax01-20261022-r2'


def pinned_report(path, size, digest, require):
    parts = path.split('/')
    require(parts[0] == '' and '..' not in parts, 'resume_report_path')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        for part in parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = nxt
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_size == size, 'resume_report_size')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(size + 1)
        require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest, 'resume_report_hash')
        return json.loads(raw)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def resume_evidence(require):
    require(os.uname().nodename.split('.')[0] == 'datax-01', 'resume_datax01_only')
    old = pinned_report(FAILED_PATH, FAILED_BYTES, FAILED_SHA, require)
    require(old.get('operation') == FAILED_OPERATION and old.get('image_import_requested') is False
            and old.get('image_import_may_be_partial') is False
            and old.get('error_code') == 'resource_based_storage_admission_failed', 'resume_not_preimport_storage_refusal')
    cleaned = pinned_report(CLEAN_PATH, CLEAN_BYTES, CLEAN_SHA, require)
    require(cleaned.get('schema') == 'portal-datax01-verified-cleanup.v1'
            and cleaned.get('services_restarted') is False and cleaned.get('database_access') is False
            and cleaned.get('containerd_commands') == 0, 'resume_cleanup_scope')
    return {'previous_operation': FAILED_OPERATION, 'previous_report_sha256': FAILED_SHA,
            'cleanup_report_sha256': CLEAN_SHA, 'cleanup_available_bytes_is_historical': True}


def install_resume(namespace):
    original_load = namespace['load_inputs']
    original_class = namespace['preparer_class']
    namespace['CONTROL'] = NEW_CONTROL
    evidence = {}

    def load():
        evidence.update(resume_evidence(namespace['require']))
        scope, resource, profile, prepared = original_load()
        # Correct the helper's inherited path so its existing 64 MiB allowance
        # is included. Preserve all original reserves and live admission gates.
        resource.CONTROL = NEW_CONTROL
        old_roles = scope.node_roles
        def roles(hostname):
            namespace['require'](hostname == 'datax-01', 'resume_datax01_only')
            return old_roles(hostname)
        scope.node_roles = roles
        return scope, resource, profile, prepared

    def controller(scope, resource, profile):
        base = original_class(scope, resource, profile)
        class Resumed(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.report['resume_evidence'] = dict(evidence)
        return Resumed

    namespace['load_inputs'] = load
    namespace['preparer_class'] = controller
