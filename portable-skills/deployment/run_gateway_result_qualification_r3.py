"""Offline real-module exercise of the six-source Gateway overlay."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
driver = runpy.run_path(str(HERE / 'test_gateway_result_overlay_r3.py'), run_name='overlay_paths_only')
sys.path[:0] = [str(driver['BASE']), str(driver['BASE'] / 'packages/harness')]
sys.meta_path.insert(0, driver['ExactOverlay']())
spec = importlib.util.spec_from_file_location('result_link_qualification', HERE / 'gateway-link-r3/qualify-energy-daily-result-link-20261111-r3.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.qualify(driver['OVERLAY'])
print(json.dumps(result, sort_keys=True))
raise SystemExit(int(result.get('status') != 'GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED'))
