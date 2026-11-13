"""Offline CLI fixtures for the isolated 2.0.3 candidate news receipts."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[3]
BASELINE = REPO / "portable-skills/energy-market-daily/scripts/daily.py"
CANDIDATE = REPO / "portable-skills/candidates/energy-market-daily-2.0.3/scripts/daily.py"
APPROVED = REPO / "portable-skills/tests/fixtures/approved.json"


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def seed(project: Path) -> dict:
    project.mkdir()
    (project / "sources").mkdir()
    approved = json.loads(APPROVED.read_text(encoding="utf-8-sig"))
    articles = []
    for section in ("market_news", "company_news"):
        # Deliberately leave one market item for the actual import-news CLI.
        quotes = approved[section][:-1] if section == "market_news" else approved[section]
        for index, quote in enumerate(quotes):
            articles.append({
                "id": f"{section}-{index}",
                "url": quote["url"],
                "source": quote["source"],
                "published": quote["published"],
                "published_at": quote["published"] + "T00:00:00+08:00",
                "retrieved_at": "2026-11-09T10:00:00+08:00",
                "evidence": [],
                "original_title": quote["title"],
                "body": quote["text"],
                "suggested_section": section,
            })
    catalog = {
        "publication_date": approved["publication_date"],
        "news_as_of": approved["news_as_of"],
        "articles": articles,
    }
    write_json(project / "research.json", catalog)
    write_json(project / "PROJECT.json", {
        "version": "2.0.2-portable",
        "publication_date": approved["publication_date"],
        "news_as_of": approved["news_as_of"],
        "input_hashes": {"research": hashlib.sha256((project / "research.json").read_bytes()).hexdigest()},
    })
    write_json(project / "news.json", {
        "publication_date": approved["publication_date"],
        "news_as_of": approved["news_as_of"],
        "market_news": [],
        "company_news": [],
    })
    quote = approved["market_news"][-1]
    (project / "sources/extra.html").write_text(
        "<html><title>" + quote["title"] + "</title><p>" + quote["text"] + "</p></html>",
        encoding="utf-8",
    )
    write_json(project / "additions.json", [{
        "url": quote["url"],
        "source": quote["source"],
        "published_at": quote["published"] + "T00:00:00+08:00",
        "original_title": quote["title"],
        "section": "market_news",
        "evidence": "sources/extra.html",
    }])
    return approved


def run_cli(entry: Path, project: Path, action: str, *args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(entry), action, "--workspace", str(project), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise AssertionError((result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8")))
    return json.loads(result.stdout.decode("utf-8"))


def command_argv(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command)
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    arguments = parser(command, ctypes.byref(count))
    if not arguments:
        raise AssertionError("CommandLineToArgvW failed")
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
        ctypes.windll.kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))


def normalized_batch(project: Path) -> dict:
    result = json.loads((project / "summary-tasks.json").read_text(encoding="utf-8"))
    result = copy.deepcopy(result)
    for section in ("market_news", "company_news"):
        for item in result[section]:
            item.pop("retrieved_at", None)  # Real import timestamps differ between CLI processes.
    return result


class CandidateReceiptTests(unittest.TestCase):
    def assert_handoff(self, receipt: dict, project: Path) -> None:
        batch = json.loads((project / "summary-tasks.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["file"], str(project / "summary-tasks.json"))
        self.assertEqual(receipt["research_file"], receipt["file"])
        self.assertEqual(receipt["news_file"], str(project / "news.json"))
        self.assertEqual(receipt["publication_date"], batch["publication_date"])
        self.assertEqual(receipt["news_as_of"], batch["news_as_of"])
        self.assertEqual(command_argv(receipt["next_command"]), [
            sys.executable, str(CANDIDATE), "build", "--workspace", str(project),
        ])

    def test_import_handoff_preserves_legacy_fields_and_generated_batch(self):
        with tempfile.TemporaryDirectory(prefix="energy receipt ") as temporary:
            root = Path(temporary)
            baseline = root / "baseline 'quoted' project"
            candidate = root / "candidate 'quoted' project"
            seed(baseline)
            seed(candidate)
            news_before = (candidate / "news.json").read_bytes()
            old = run_cli(BASELINE, baseline, "import-news", "--sources", str(baseline / "additions.json"))
            new = run_cli(CANDIDATE, candidate, "import-news", "--sources", str(candidate / "additions.json"))
            for key in ("status", "added", "article_count"):
                self.assertEqual(new[key], old[key])
            self.assertEqual(new["status"], "VERIFIED_SOURCE_EVIDENCE_IMPORTED")
            self.assertEqual(normalized_batch(candidate), normalized_batch(baseline))
            self.assertEqual((candidate / "news.json").read_bytes(), news_before)
            self.assert_handoff(new, candidate)

    def test_default_select_metadata_matches_existing_summary_file(self):
        with tempfile.TemporaryDirectory(prefix="energy receipt ") as temporary:
            project = Path(temporary) / "selected 'quoted' project"
            seed(project)
            run_cli(CANDIDATE, project, "import-news", "--sources", str(project / "additions.json"))
            before = normalized_batch(project)
            result = run_cli(CANDIDATE, project, "select")
            self.assertEqual(result["status"], "SUMMARY_BATCH_READY")
            self.assertEqual((result["market_count"], result["company_count"]), (3, 5))
            self.assertEqual(normalized_batch(project), before)
            self.assert_handoff(result, project)

    def test_explicit_select_retains_ids_order_and_validation(self):
        with tempfile.TemporaryDirectory(prefix="energy receipt ") as temporary:
            project = Path(temporary) / "explicit 'quoted' project"
            seed(project)
            run_cli(CANDIDATE, project, "import-news", "--sources", str(project / "additions.json"))
            catalog = json.loads((project / "research.json").read_text(encoding="utf-8"))
            market_ids = [a["id"] for a in catalog["articles"] if a["suggested_section"] == "market_news"][::-1]
            company_ids = [a["id"] for a in catalog["articles"] if a["suggested_section"] == "company_news"][::-1]
            result = run_cli(CANDIDATE, project, "select", "--market-ids", ",".join(market_ids), "--company-ids", ",".join(company_ids))
            batch = normalized_batch(project)
            self.assertEqual([a["id"] for a in batch["market_news"]], market_ids)
            self.assertEqual([a["id"] for a in batch["company_news"]], company_ids)
            self.assert_handoff(result, project)
            invalid = subprocess.run(
                [sys.executable, "-I", "-B", str(CANDIDATE), "select", "--workspace", str(project),
                 "--market-ids", ",".join([market_ids[0]] * 3), "--company-ids", ",".join(company_ids)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
            )
            self.assertEqual(invalid.returncode, 1)
            self.assertEqual(json.loads(invalid.stdout)["error_code"], "NEWS_SELECTION_ID_INVALID")
            self.assertEqual(normalized_batch(project), batch)


if __name__ == "__main__":
    unittest.main()
