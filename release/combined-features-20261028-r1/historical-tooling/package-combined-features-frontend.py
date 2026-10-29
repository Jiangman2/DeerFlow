"""Package the sealed batch frontend while retaining all accepted rollback refs."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('polish_package', HERE / 'package-frontend-polish-20261028.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def build(qualified, output):
    releases = dict(previous.previous.spacing.PROTECTED)
    releases.update({
        '20261024-chat-notice-layout-r1': 'sha256:cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298',
        '20261028-frontend-polish-r1': 'sha256:1d0f917d767bcb80e9dbb6e9a6281b6730442511fcb1a1e3a35b203f049daf3b',
    })
    protected = {}
    for tag, digest in releases.items():
        protected['deerflow.local/deer-flow-frontend:' + tag] = digest
        protected['deerflow.local/deer-flow-frontend@' + digest] = digest
    return previous.previous.spacing.package.build(
        qualified, output, tag='deerflow.local/deer-flow-frontend:20261028-combined-features-r1',
        release='combined-features', revision='20261028-r1', extra_protected=protected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.qualified, args.output)
