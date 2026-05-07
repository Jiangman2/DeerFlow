from __future__ import annotations

import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    out = (cp.stdout or cp.stderr).strip()
    return cp.returncode, out


def main() -> int:
    project = Path("/app/projects/native_doc_driven_fullchain_ppt169_20260504")
    scripts = Path("/app/skills/public/ppt-master/scripts")
    work = Path("/tmp/ppt-real-smoke")
    work.mkdir(parents=True, exist_ok=True)

    # Fixtures for source_to_md real conversions
    (work / "sample.html").write_text(
        "<html><body><h1>Demo</h1><p>doc_to_md real smoke test.</p></body></html>",
        encoding="utf-8",
    )

    run(
        [
            "python3",
            "-c",
            (
                "import xlsxwriter; "
                "wb=xlsxwriter.Workbook('/tmp/ppt-real-smoke/sample.xlsx'); "
                "ws=wb.add_worksheet('Sheet1'); "
                "ws.write(0,0,'name'); ws.write(0,1,'value'); "
                "ws.write(1,0,'alpha'); ws.write(1,1,42); wb.close()"
            ),
        ]
    )
    run(
        [
            "python3",
            "-c",
            (
                "import fitz; "
                "doc=fitz.open(); p=doc.new_page(); "
                "p.insert_text((72,72),'pdf_to_md real smoke test'); "
                "doc.save('/tmp/ppt-real-smoke/sample.pdf')"
            ),
        ]
    )

    export_candidates = sorted((project / "exports").glob("*.pptx"))
    source_pptx = export_candidates[-1] if export_candidates else None

    tests: list[tuple[str, list[str], int]] = [
        ("project info", ["python3", str(scripts / "project_manager.py"), "info", str(project)], 60),
        ("project validate", ["python3", str(scripts / "project_manager.py"), "validate", str(project)], 60),
        ("check annotations", ["python3", str(scripts / "check_annotations.py"), str(project)], 60),
        ("svg quality", ["python3", str(scripts / "svg_quality_checker.py"), str(project)], 120),
        ("split notes", ["python3", str(scripts / "total_md_split.py"), str(project)], 120),
        ("finalize svg", ["python3", str(scripts / "finalize_svg.py"), str(project)], 120),
        ("export pptx", ["python3", str(scripts / "svg_to_pptx.py"), str(project), "-s", "final"], 180),
        (
            "update spec dry-run",
            ["python3", str(scripts / "update_spec.py"), str(project), "colors.primary=#1E3A8A", "--dry-run"],
            60,
        ),
        (
            "chart calc bar",
            [
                "python3",
                str(scripts / "svg_position_calculator.py"),
                "calc",
                "bar",
                "--data",
                "A:185,B:142",
                "--area",
                "130,155,1200,480",
                "--bar-width",
                "120",
            ],
            60,
        ),
        ("analyze images", ["python3", str(scripts / "analyze_images.py"), str(project / "images")], 60),
        (
            "web to md",
            ["python3", str(scripts / "source_to_md" / "web_to_md.py"), "https://example.com", "-o", str(work / "web.md")],
            120,
        ),
        (
            "doc to md",
            ["python3", str(scripts / "source_to_md" / "doc_to_md.py"), str(work / "sample.html"), "-o", str(work / "html.md")],
            120,
        ),
        (
            "excel to md",
            ["python3", str(scripts / "source_to_md" / "excel_to_md.py"), str(work / "sample.xlsx"), "-o", str(work / "excel.md")],
            120,
        ),
        (
            "pdf to md",
            ["python3", str(scripts / "source_to_md" / "pdf_to_md.py"), str(work / "sample.pdf"), "-o", str(work / "pdf.md")],
            120,
        ),
        ("notes voices", ["python3", str(scripts / "notes_to_audio.py"), "--list-common-voices"], 120),
        (
            "image search openverse",
            [
                "python3",
                str(scripts / "image_search.py"),
                "offshore platform",
                "--filename",
                "offshore.jpg",
                "--provider",
                "openverse",
                "-o",
                str(work),
            ],
            180,
        ),
    ]

    if source_pptx:
        tests.extend(
            [
                (
                    "ppt to md",
                    ["python3", str(scripts / "source_to_md" / "ppt_to_md.py"), str(source_pptx), "-o", str(work / "ppt.md")],
                    180,
                ),
                (
                    "template import manifest-only",
                    [
                        "python3",
                        str(scripts / "pptx_template_import.py"),
                        str(source_pptx),
                        "--manifest-only",
                        "-o",
                        str(work / "template_import"),
                    ],
                    180,
                ),
            ]
        )

    ok = 0
    fail = 0
    rows: list[tuple[str, bool, int, str]] = []
    for name, cmd, timeout in tests:
        try:
            code, out = run(cmd, timeout=timeout)
            is_ok = code == 0
        except Exception as exc:
            code = -999
            out = str(exc)
            is_ok = False
        if is_ok:
            ok += 1
        else:
            fail += 1
        rows.append((name, is_ok, code, "\n".join(out.splitlines()[:5])))

    print(f"REAL_SMOKE TOTAL {len(rows)} OK {ok} FAIL {fail}")
    for name, is_ok, code, head in rows:
        status = "OK" if is_ok else "FAIL"
        print(f"[{status}] {name} (code={code})")
        if not is_ok:
            print(head[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
