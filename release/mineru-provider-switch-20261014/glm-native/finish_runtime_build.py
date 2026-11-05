"""Reuse completed native outputs, run only runtime/OCI gates under frozen controls."""
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_weknora as original
from build_guard import require, runtime_archive, safe_file
from isolated_buildkit import json_receipt, write_json
from prepare_oci import fresh_directory


class RuntimeBuilder(original.Builder):
    def build(self):
        plan = self.plan
        require(plan['runtime_finish']['compile_reused'] is True, 'compile_reuse_receipt_required')
        runtime = plan['runtime_base']
        base, br = self.stage_base('runtime-base', runtime_archive(plan), {
            'source_image': runtime['image'], 'source_image_id': runtime['config_digest'],
            'archive_sha256': runtime['archive_sha256']})
        require(br['manifest_digest'] == runtime['manifest_digest'] and br['layers_verified'] == 20,
                'runtime_base_changed')
        bases = {'runtime-base': (base, br['manifest_digest'])}
        def cmd(context, **kw):
            return original.arguments(self.binary['buildctl'], self.address, context, self.cgroup, bases, **kw)
        canary = fresh_directory(self.output/'canary')
        token = 'ppt_runtime_resource_ready_'+original.uuid.uuid4().hex
        (canary/'Dockerfile.weknora').write_text('FROM weknora-glm-runtime-base AS candidate\n'
            'RUN --network=none python3 -I -B -c "import time; print(\''+token+
            '\',flush=True); time.sleep(25)"\n', encoding='ascii')
        self.phase('resource-canary', cmd(canary)+['--no-cache'], canary=token)
        context = self.output/'context'
        json_receipt(self.phase('stage-runtime-payload', [sys.executable, '-B', self.package/'stage_context.py',
            '--archive', self.package/'context.tar', '--output', context,
            '--sha256', plan['context_sha256']]), 'context_stage')
        image = 'deerflow.local/weknora-app:20261016-native-glm-runtimefix-'+plan['context_sha256'][:16]
        partial, meta = self.output/'weknora.candidate.oci.tar.partial', self.output/'build-metadata.json'
        self.phase('runtime-only-no-recompile', cmd(context, output=partial, image=image, metadata=meta))
        for path in (partial, meta):
            s = path.lstat()
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1, 'unsafe_export')
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
            try:
                t = os.fstat(fd)
                require((s.st_dev, s.st_ino) == (t.st_dev, t.st_ino), 'export_raced')
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        safe_file(partial, 3*original.GIB)
        proof = self.output/'qualification'
        self.phase('export-test-evidence', cmd(context, target='qualification-evidence', output=proof))
        result = json_receipt(self.phase('verify-runtime-candidate', [sys.executable, '-B',
            self.package/'verify_runtime_candidate.py', '--archive', partial, '--output', self.output/'candidate-check',
            '--metadata', meta, '--base', base, '--base-receipt', self.output/'runtime-base-receipt.json',
            '--plan', self.package/'BUILD-PLAN.json', '--evidence', proof, '--image', image]), 'audited_oci_base')
        self.guard(source=True, force=True)
        final = partial.with_name('weknora.candidate.oci.tar')
        require(not final.exists(), 'candidate_exists')
        partial.rename(final)
        return {'weknora_glm_candidate_build': 'PASS', 'component': result, 'archive': str(final),
            'production_baseline': plan['production_baseline'], 'compile_reused': True,
            'production_workloads_mutated': False, 'images_imported': False, 'provider_requests': 0,
            'model_configuration_changed': False, 'consumer_slots_changed': False,
            'database_migrations': [], 'ready_for_rollout': False,
            'native_intranet_acceptance': 'REQUIRED', 'enrichment_acceptance': 'REQUIRED'}


if __name__ == '__main__':
    original.Builder = RuntimeBuilder
    try:
        original.main()
    except Exception as exc:
        print(json.dumps({'weknora_runtime_finish': 'FAIL', 'stage': original.STAGE,
            'error_type': type(exc).__name__, 'check': str(exc) if isinstance(exc, original.CheckFailure)
            else 'SAFE_DIAGNOSTIC_REQUIRED', 'automatic_retry': False, 'production_writes': 0,
            'images_imported': False, 'provider_requests': 0, 'ready_for_rollout': False}), flush=True)
        sys.exit(1)
