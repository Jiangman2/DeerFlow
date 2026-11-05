"""Package the mobile spacing correction with the qualified frontend importers."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('notice_package', HERE / 'package-nonconfidential-notice-frontend.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
PROTECTED = {
    '20261023-nonconfidential-notice-r2': 'sha256:b37036e9b226f8605e3da98d9f81cb072fc30da29cb5b972702c5ed6ec535638',
    '20261024-mobile-notice-removal-r1': 'sha256:0af4bb16a0924a02f6cfcb927c137642329b5831d01a5eded8c4f61ee8ad43f7',
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    protected = {}
    for tag, digest in PROTECTED.items():
        protected['deerflow.local/deer-flow-frontend:' + tag] = digest
        protected['deerflow.local/deer-flow-frontend@' + digest] = digest
    package.build(args.qualified, args.output,
                  tag='deerflow.local/deer-flow-frontend:20261024-mobile-chat-spacing-r1',
                  release='mobile-chat-spacing', revision='20261024-r1', extra_protected=protected)
