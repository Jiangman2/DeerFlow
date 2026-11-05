"""Fail-closed checks for the deliberately small synthetic test runtime."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import round3_qualifier_template as runner
import round3_qualifier_builder as builder
import round3_qualification_plan as plan
import round3_release_builder as release

class RunnerTests(unittest.TestCase):
    def test_raises_requires_exception(self):
        with self.assertRaisesRegex(AssertionError, 'DID NOT RAISE'):
            with runner.Raises(ValueError):
                pass

    def test_raises_does_not_swallow_other_exception_or_wrong_message(self):
        with self.assertRaises(TypeError):
            with runner.Raises(ValueError):
                raise TypeError('wrong')
        with self.assertRaisesRegex(AssertionError, 'did not match'):
            with runner.Raises(ValueError, match='required'):
                raise ValueError('different')

    def test_raises_accepts_expected_type_and_regex(self):
        with runner.Raises(ValueError, match='required.*value'):
            raise ValueError('required missing value')

    def test_scalar_approx_preserves_tolerance_and_nonfinite_semantics(self):
        self.assertTrue(1/3 == runner.Approx(0.3333333333333333))
        self.assertFalse(.34 == runner.Approx(1/3))
        self.assertFalse(float('nan') == runner.Approx(float('nan')))
        self.assertFalse(float('inf') == runner.Approx(1))

    def test_unknown_pytest_apis_fail_closed(self):
        with patch.dict(sys.modules):
            runner.install_pytest_subset()
            with self.assertRaises(AttributeError):
                sys.modules['pytest'].skip('cannot skip')
            with self.assertRaises(AttributeError):
                sys.modules['pytest'].mark.xfail

    def synthetic(self, source):
        mod = types.ModuleType('synthetic_parameters')
        mod.parametrize = runner.parametrize
        exec(source, mod.__dict__)
        return mod

    def test_stacked_parameters_execute_full_cartesian_product(self):
        mod = self.synthetic("@parametrize('a',[1,2])\n@parametrize(('b','c'),[(3,4),(5,6)])\ndef test_case(a,b,c,tmp_path): pass")
        cases, counts = runner.collect([mod])
        self.assertEqual(counts, {'synthetic_parameters::test_case':4})
        self.assertEqual({tuple(sorted(c[2].items())) for c in cases},
                         {(('a',a),('b',b),('c',c)) for a in (1,2) for b,c in ((3,4),(5,6))})
        self.assertTrue(all(c[3] for c in cases))

    def test_fixture_expansion_does_not_silently_drop_required_arguments(self):
        for source in ("def test_case(monkeypatch): pass", "def test_case(*args): pass"):
            with self.assertRaisesRegex(ValueError,'UNSUPPORTED_'):
                runner.collect([self.synthetic(source)])

    def test_empty_or_duplicate_parameters_refused(self):
        with self.assertRaisesRegex(ValueError,'EMPTY_PARAMETER'):
            runner.parametrize('a',[])
        with self.assertRaisesRegex(ValueError,'DUPLICATE_PARAMETER'):
            self.synthetic("@parametrize('a',[1])\n@parametrize('a',[2])\ndef test_case(a): pass")

    def test_parameter_signature_detects_changed_value_and_function_identity(self):
        mod = self.synthetic('MISSING=object()\ndef action(case): pass')
        self.assertNotEqual(runner.parameter_signature({'x':1},mod),runner.parameter_signature({'x':2},mod))
        self.assertEqual(runner.parameter_signature(mod.MISSING,mod),['module_sentinel','synthetic_parameters',['MISSING']])
        self.assertEqual(runner.parameter_signature(mod.action,mod)[0], 'function')
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_PARAMETER'):
            runner.parameter_signature(object(),mod)

    def test_collection_evidence_tamper_refused(self):
        current = json.loads((HERE/'qualification-plan.json').read_bytes())
        collection = json.loads((HERE/'pytest-collection.json').read_bytes())
        collection['cases'][0]['case'] += '-changed'
        with self.assertRaisesRegex(ValueError,'COLLECTION_CASES_CHANGED'):
            builder.build(current,collection)

    def test_final_candidate_approval_rejects_previous_doc_hash_before_qualifier(self):
        after = json.loads((HERE/'candidate-manifest.json').read_bytes())
        before = json.loads((HERE.parent/'research-fund-feedback-20261105/candidate-manifest.json').read_bytes())
        with self.assertRaisesRegex(ValueError,'FINAL_CANDIDATE_APPROVAL_CHANGED'):
            release.prepare_bundle(None,before,after,None,None,None,None,
                approved_candidate_tree_sha256='0d8d4b6c4855951f1821e1ecc59276353e34febd3635f5b289f553b944839db9',
                qualification_plan=None,qualification_receipt=None)

if __name__ == '__main__':
    unittest.main()
