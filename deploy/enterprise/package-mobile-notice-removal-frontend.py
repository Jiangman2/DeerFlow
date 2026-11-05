"""Package the mobile-only frontend change using the field-proven importers."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('notice_package', HERE / 'package-nonconfidential-notice-frontend.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
OLD = 'sha256:b37036e9b226f8605e3da98d9f81cb072fc30da29cb5b972702c5ed6ec535638'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package.build(args.qualified, args.output,
                  tag='deerflow.local/deer-flow-frontend:20261024-mobile-notice-removal-r1',
                  release='mobile-notice-removal', revision='20261024-r1',
                  extra_protected={'deerflow.local/deer-flow-frontend@' + OLD: OLD,
                                   'deerflow.local/deer-flow-frontend:20261023-nonconfidential-notice-r2': OLD})
