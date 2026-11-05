"""Verify archived Gateway 406 / Frontend 239 observations without production I/O."""
import argparse
import json

from production_observation_406_20261103 import reader, verify


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="Read committed Git objects; default reads the working tree.")
    parser.add_argument("--check-current", action="store_true",
                        help="Also require current sources to match the observed commit; default checks historical evidence.")
    args = parser.parse_args()
    result = verify(reader(args.ref, args.check_current))
    result["source_binding"] = "current_revision" if args.check_current else "historical_observed_commit"
    print(json.dumps(result, sort_keys=True))
