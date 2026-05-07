from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> int:
    base = Path("/app/skills/public/ppt-master/scripts")
    entries: list[Path] = []
    entries.extend(sorted(base.glob("*.py")))
    entries.extend(sorted((base / "source_to_md").glob("*.py")))
    entries = [p for p in entries if not p.name.startswith("_")]

    results = []
    for script in entries:
        cmd = ["python3", str(script), "--help"]
        try:
            cp = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            out = (cp.stdout or cp.stderr).strip().splitlines()
            results.append(
                {
                    "script": str(script),
                    "ok": cp.returncode == 0,
                    "code": cp.returncode,
                    "head": "\n".join(out[:6]),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "script": str(script),
                    "ok": False,
                    "code": -999,
                    "head": str(exc),
                }
            )

    ok_count = sum(1 for r in results if r["ok"])
    print(f"TOTAL {len(results)} OK {ok_count} FAIL {len(results) - ok_count}")
    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        print(f"[{status}] {r['script']} (code={r['code']})")
        if not r["ok"]:
            print(r["head"][:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
