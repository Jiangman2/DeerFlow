import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
prior = HERE.parent / 'native-remaining-preload-r1'
assert hashlib.sha256((prior / 'resume_remaining.py').read_bytes()).hexdigest() == '438d6485e32d40c120f9ca81d69e21aec91a76d0a6a79a70873219b31c75a798'
supervisor = (HERE.parent / 'native-gateway-recovery-r1/recover_gateway.py').read_bytes()
assert hashlib.sha256(supervisor).hexdigest() == '13b18107c4be2018d74e6b5d4fea00ab36b46ff091a4a601958da593dfbb5cec'
success_raw = (HERE.parent / 'native-gateway-recovery-evidence-r1/REPORT.json').read_bytes()
partial_raw = (HERE.parent / 'native-remaining-preload-evidence-r1/REPORT.json').read_bytes()
assert len(success_raw) == 6504 and hashlib.sha256(success_raw).hexdigest() == 'c0732346dcb41b9a979984b1036bf67c05ce314c030551246f5c7ec3f20ab662'
assert len(partial_raw) == 8881 and hashlib.sha256(partial_raw).hexdigest() == '77823e4308eac6d38f5576f463342591750833b3555681472d9804eae7e1a417'
source = (prior / 'continue_remaining_template.py').read_text(encoding='utf-8')
source = source.replace('four remaining', 'three remaining').replace('continue_four', 'continue_three')
source = source.replace("ROLES = ('gateway-rollback-compatible', 'ppt-master-runner-forward',", "ROLES = ('ppt-master-runner-forward',")
source = source.replace("PREVIOUS = ('frontend', 'gateway-forward')", "PREVIOUS = ('frontend', 'gateway-forward', 'gateway-rollback-compatible')")
source = source.replace("portal-ppt-remaining-preload-r1-", "portal-ppt-remaining-three-r1-")
source = source.replace("portal-ppt-remaining-preload-'", "portal-ppt-remaining-three-'")
source = source.replace("schema='portal-native-remaining-preload.v1'", "schema='portal-native-remaining-three.v1'")
source = source.replace("'REMAINING-FOUR-PASS.json'", "'REMAINING-THREE-PASS.json'")
source = source.replace("'--resume-remaining-once'", "'--resume-three-once'")
source = source.replace("gateway_forward_reimported=False, phase_a_repeated=False", "gateway_forward_reimported=False, gateway_rollback_reimported=False, phase_a_repeated=False")
source = source.replace("        plan, receipts = guard.inspect_checkpoint(module, profile)", "        result['remaining_four_boundary'] = partial_boundary(supervisor, module, profile, host)\n        plan, receipts = guard.inspect_checkpoint(module, profile)")
start = source.index('def snapshot_gate('); end = source.index('def captured_role(', start)
source = source[:start] + (HERE / 'snapshot_gate_part.py').read_text(encoding='utf-8') + '\n\n' + (HERE / 'partial_boundary_part.py').read_text(encoding='utf-8') + '\n\n' + source[end:]
constants = "PARTIAL_REPORT_SHA = '__PARTIAL_REPORT_SHA__'\nPARTIAL_REPORT_BYTES = 8881\nPARTIAL_EVIDENCE = Path('__PARTIAL_EVIDENCE__')\nPARTIAL_ONCE = Path('/var/lib/portal-ppt-remaining-preload-r1-' + OP + '.STARTED.json')\n"
source = source.replace("ROOT = Path('/var/lib')\n", "ROOT = Path('/var/lib')\n" + constants)
(HERE / 'resume_three_template.py').write_text(source, encoding='utf-8', newline='\n')
replacements = {'__SUPERVISOR_Z64__': base64.b64encode(zlib.compress(supervisor, 9)).decode(),
                '__SUCCESS_REPORT_SHA__': hashlib.sha256(success_raw).hexdigest(), '__SUCCESS_EVIDENCE__': json.loads(success_raw)['evidence'],
                '__PARTIAL_REPORT_SHA__': hashlib.sha256(partial_raw).hexdigest(), '__PARTIAL_EVIDENCE__': json.loads(partial_raw)['evidence']}
for old, new in replacements.items():
    assert source.count(old) == 1
    source = source.replace(old, new)
raw = source.encode(); compile(raw, 'resume_three.py', 'exec')
(HERE / 'resume_three.py').write_bytes(raw)
print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())))
