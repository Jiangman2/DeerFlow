"""Package the combined desktop notice and mobile spacing update."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('spacing_package', HERE / 'package-mobile-chat-spacing-frontend.py')
spacing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spacing)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    protected = {}
    for tag, digest in spacing.PROTECTED.items():
        protected['deerflow.local/deer-flow-frontend:' + tag] = digest
        protected['deerflow.local/deer-flow-frontend@' + digest] = digest
    spacing.package.build(args.qualified, args.output,
                          tag='deerflow.local/deer-flow-frontend:20261024-chat-notice-layout-r1',
                          release='chat-notice-layout', revision='20261024-r1', extra_protected=protected)
