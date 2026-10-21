"""Bind the prepared Phase B guard to the actual reviewed inventory; no execution."""
import ast
import hashlib
import json
from pathlib import Path
import pprint
import re

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
INVENTORY = Path('C:/Users/15285/Downloads/REPORT(4).json')
INVENTORY_SHA = 'ccfb0e3efe75efab543da7a6596fd8137662a30f4a5abbf6f85660f7cac0105a'
PREPARED_SHA = '19dcd66747c9496beafe35ec1d306d0f8981dddc4ccbcf01ce984a59935ae919'


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


raw = INVENTORY.read_bytes()
require(len(raw) == 89968 and sha(raw) == INVENTORY_SHA, 'inventory_identity')
inventory = json.loads(raw)
checkpoint = inventory['checkpoint']
require(inventory['operation'] == 'abf96dde50174b1eb7caf70a4e6711b1'
        and inventory['collected_at_utc'] == '2026-10-17T12:48:42Z', 'inventory_binding')
require(checkpoint['state_unchanged'] is True and checkpoint['any_phase_b_receipt'] is False
        and checkpoint['actual_checkpoint_file_count'] == checkpoint['expected_checkpoint_file_count'] == 24
        and checkpoint['unexpected_names_sha256'] == [], 'checkpoint_not_exact')
require(checkpoint['plan_wire'] == {
    'bytes': 122307, 'fixed_image_structure_valid': True, 'matches_accepted': True,
    'sha256': 'b3f08cc0ca0f8cb75488ce37a26531071699f5eb5d9097687604851753d72aa9'}, 'wire_binding')
sealed = checkpoint['sealed_directory']
require((sealed['device'], sealed['inode'], sealed['uid'], sealed['mode']) == (64785, 14340598, 0, '0o700')
        and sealed['identity_matches'] is True, 'sealed_identity')
profile_raw = (PREP / 'native-preload-r1/PROFILE.json').read_bytes()
require(sha(profile_raw) == 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2', 'profile_identity')
profile = json.loads(profile_raw)
names = {'STARTED.json', 'PREPARED.json', 'RUN-REQUESTED.json', 'FAIL.json', 'PHASE-A-PASS.json', 'PHASE-A-REQUESTED.json'}
for row in profile['images']:
    names.update(row['role'] + suffix for suffix in ('.SEALED.json', '.PHASE-A-PASS.json', '.PHASE-A-REQUESTED.json'))
present = [row for row in checkpoint['files'] if row['present']]
require(len(present) == 24 and {row['name'] for row in present} == names, 'checkpoint_file_set')
pins = {}
for row in present:
    require(type(row['bytes']) is int and row['bytes'] > 0 and re.fullmatch('[a-f0-9]{64}', row['sha256']), 'pin_shape')
    pins[row['name']] = dict(bytes=row['bytes'], sha256=row['sha256'])
pins = dict(sorted(pins.items()))
require(pins['FAIL.json'] == dict(bytes=6151, sha256='4a9f9824ca153f962a8d7191342811b03f8d6c9ef799b2f388ca51b5f1d9833a'), 'failure_identity')
prepared = (HERE / 'resume_phase_b.py').read_bytes()
require(sha(prepared) == PREPARED_SHA, 'prepared_candidate_changed')
source = prepared.decode('utf-8')
require(source.count('CHECKPOINT_SHA256 = {}') == 1 and source.count("RELEASE_STATE = 'PREPARATION_NOT_DELIVERED'") == 1, 'binding_sites')
bound = source.replace('CHECKPOINT_SHA256 = {}', 'CHECKPOINT_SHA256 = ' + pprint.pformat(pins, sort_dicts=True, width=100))
bound = bound.replace("RELEASE_STATE = 'PREPARATION_NOT_DELIVERED'", "RELEASE_STATE = 'REVIEWED_FOR_MANUAL_EXECUTION'\n"
                      + 'INVENTORY_SHA256 = ' + repr(INVENTORY_SHA) + '\n'
                      + 'INVENTORY_COLLECTED_AT_UTC = ' + repr(inventory['collected_at_utc']) + '\n'
                      + "INVENTORY_SCOPE = 'HISTORICAL_CHECKPOINT_BINDING_NOT_A_FRESH_STORAGE_RESERVATION'")
initial_old = "image_import_may_be_partial=None, target_import_requested=False, phase_b='NOT_REQUESTED')"
initial_new = "image_import_may_be_partial=None, target_import_requested=None, phase_b=None,\n" \
              "                  state_scope='THIS_PHASE_B_CONTINUATION', prior_phase_a='UNKNOWN_UNTIL_LOCKED_CHECKPOINT_CHECK')"
boundary = "        require(not os.path.lexists(ONCE), 'phase_b_continuation_already_started')\n"
boundary_update = "        result.update(target_import_requested=False, image_import_may_be_partial=False,\n" \
                  "                      phase_b='NOT_REQUESTED', prior_phase_a='PASS')\n"
require(bound.count(initial_old) == bound.count(boundary) == 1, 'state_patch_sites')
bound = bound.replace(initial_old, initial_new).replace(boundary, boundary + boundary_update)
binding_names = {'CHECKPOINT_SHA256', 'RELEASE_STATE', 'INVENTORY_SHA256', 'INVENTORY_COLLECTED_AT_UTC', 'INVENTORY_SCOPE'}


def executable_ast(text):
    tree = ast.parse(text, feature_version=(3, 7))
    tree.body = [node for node in tree.body if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name) and node.targets[0].id in binding_names)]
    return ast.dump(tree, include_attributes=False)


# The only executable delta requested by independent review is conservative state
# reporting: null before locked proof, false/NOT_REQUESTED only after proof and no once.
state_corrected_source = source.replace(initial_old, initial_new).replace(boundary, boundary + boundary_update)
require(executable_ast(state_corrected_source) == executable_ast(bound), 'unexpected_executable_logic_changed')
destination = HERE / 'resume_phase_b_bound.py'
destination.write_bytes(bound.encode('utf-8'))
record = {
    'schema': 'portal-native-phase-b-bound-candidate.v1', 'status': 'BOUND_CANDIDATE_AWAITING_ROOT_RELEASE',
    'prepared': {'bytes': len(prepared), 'sha256': PREPARED_SHA},
    'candidate': {'path': str(destination), 'bytes': len(bound.encode('utf-8')), 'sha256': sha(bound.encode('utf-8'))},
    'inventory': {'bytes': len(raw), 'sha256': INVENTORY_SHA, 'collected_at_utc': inventory['collected_at_utc'],
                  'fresh_at_execution_assumed': False, 'checkpoint_file_count': len(pins)},
    'checkpoint_pins': pins,
    'delta_validation': {'executable_ast_equal_after_binding_assignments_and_exact_reviewed_state_reporting_delta': True,
                         'state_reporting_delta': 'UNKNOWN until same-lock checkpoint proof and no successor once; fields scoped to this Phase B continuation',
                         'python37_syntax': True, 'old_fixture_suite_repeated': False,
                         'original_storage_policy_unchanged': True, 'fresh_storage_and_content_gates_preserved': True},
    'server_execution': False, 'image_deletion': False, 'image_archival': False, 'ready_for_rollout': False
}
(HERE / 'BOUND-CANDIDATE.json').write_bytes((json.dumps(record, indent=2, sort_keys=True) + '\n').encode())
print(json.dumps({'candidate': record['candidate'], 'inventory': record['inventory'], 'delta_validation': record['delta_validation']}, sort_keys=True))
