"""Private subprocess entry point. Install limits before importing converters."""

import os
import sys
from pathlib import Path


def main():
    import resource

    source, converter, target, memory, output, cpu = sys.argv[1:]
    for kind, cap in ((resource.RLIMIT_AS, int(memory)), (resource.RLIMIT_FSIZE, int(output)), (resource.RLIMIT_CPU, int(cpu)), (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (cap, cap))
    # Invoke the installed package from this worker's own harness root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from deerflow.utils.file_conversion import _do_convert

    text = _do_convert(Path(source), converter, bounded_threads=True)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty document conversion")
    with open(target, "xb") as stream:
        total = 0
        for start in range(0, len(text), 65536):
            chunk = text[start : start + 65536].encode("utf-8")
            total += len(chunk)
            if total > int(output):
                raise ValueError("Document conversion output limit exceeded")
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never export raw document data or SDK exceptions to Gateway logs.
        sys.exit(1)
