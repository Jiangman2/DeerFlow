"""Offline qualification of the history composer candidate, with no deployment I/O."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile

import history_composer_source_20261106 as source
import datalake_combined_source_20261112 as datalake

ROOT = Path(__file__).resolve().parents[2]


def verify(ref=None):
    if ref:
        extra = []
        if subprocess.check_output(["git", "ls-tree", "--name-only", ref, datalake.MANIFEST], cwd=ROOT).strip():
            extra.extend((datalake.ARCHIVE, *datalake.PATHS))
        raw = subprocess.check_output(["git", "archive", ref, source.ARCHIVE, *sorted(source.PATHS), *extra], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
        read = files.__getitem__
    else:
        read = lambda path: (ROOT / path).read_bytes()
    read, datalake_result = datalake.historical_view(read)
    qualification = source.qualify(read)
    assert qualification, "HISTORY_COMPOSER_MANIFEST_REQUIRED"
    return dict(source.summary(qualification), source_verified=True, datalake_source=datalake_result,
                historical_inventories_modified=False, production_commands_executed=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref")
    print(json.dumps(verify(parser.parse_args().ref), sort_keys=True))
