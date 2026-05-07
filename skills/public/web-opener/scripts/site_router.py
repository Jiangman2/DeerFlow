#!/usr/bin/env python3
"""Resolve site names to URLs and return stable markdown links for front-end opening."""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def _default_sites_path() -> Path:
    return (Path(__file__).resolve().parent.parent / "sites.json").resolve()


def _normalize(text: str) -> str:
    lowered = text.strip().lower()
    return re.sub(r"\s+", "", lowered)


def load_sites(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return data.get("sites", [])


def save_sites(path: Path, sites: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sites": sites}, ensure_ascii=False, indent=2), encoding="utf-8")


def score_site(query: str, site: dict[str, Any]) -> float:
    qn = _normalize(query)
    name = str(site.get("name", ""))
    aliases = [str(x) for x in site.get("aliases", [])]
    candidates = [name] + aliases
    best = 0.0
    for candidate in candidates:
        cn = _normalize(candidate)
        if not cn:
            continue
        if qn == cn:
            return 1.0
        if qn in cn or cn in qn:
            best = max(best, 0.92)
        best = max(best, SequenceMatcher(None, qn, cn).ratio())
    return best


def find_best(query: str, sites: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for site in sites:
        scored.append((score_site(query, site), site))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [{"score": round(s, 4), **site} for s, site in scored[:5] if s > 0]
    if scored and scored[0][0] >= 0.65:
        return scored[0][1], top
    return None, top


def cmd_add(args: argparse.Namespace, sites_path: Path) -> int:
    sites = load_sites(sites_path)
    aliases = [x.strip() for x in (args.add_aliases or "").split(",") if x.strip()]
    record = {
        "name": args.add_name.strip(),
        "url": args.add_url.strip(),
        "aliases": aliases,
        "description": (args.add_description or "").strip(),
    }

    replaced = False
    for i, site in enumerate(sites):
        if _normalize(site.get("name", "")) == _normalize(record["name"]):
            sites[i] = record
            replaced = True
            break
    if not replaced:
        sites.append(record)
    save_sites(sites_path, sites)
    action = "updated" if replaced else "added"
    print(json.dumps({"ok": True, "action": action, "site": record}, ensure_ascii=False))
    return 0


def cmd_list(sites_path: Path) -> int:
    sites = load_sites(sites_path)
    print(json.dumps({"ok": True, "count": len(sites), "sites": sites}, ensure_ascii=False))
    return 0


def cmd_query(args: argparse.Namespace, sites_path: Path) -> int:
    sites = load_sites(sites_path)
    if not sites:
        print(json.dumps({"ok": False, "error": "No sites configured", "sites_path": str(sites_path)}, ensure_ascii=False))
        return 1
    best, candidates = find_best(args.query, sites)
    if not best:
        print(
            json.dumps(
                {
                    "ok": False,
                    "matched": False,
                    "query": args.query,
                    "message": "No confident match found",
                    "candidates": candidates,
                },
                ensure_ascii=False,
            )
        )
        return 2

    out = {
        "ok": True,
        "matched": True,
        "query": args.query,
        "site": best,
        "markdown_link": f"[{best.get('name')}]({best.get('url')})",
        "action": "return_link_only",
        "opened": False,
        "open_method": "frontend_click",
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Site router for web-opener skill")
    parser.add_argument("--sites", default=str(_default_sites_path()), help="Path to sites.json")
    parser.add_argument("--query", help="Natural language query/site name")
    parser.add_argument(
        "--open",
        action="store_true",
        help="Deprecated: retained for compatibility; current strategy always returns link only",
    )

    parser.add_argument("--list", action="store_true", help="List all configured sites")
    parser.add_argument("--add-name", help="Add or update site by name")
    parser.add_argument("--add-url", help="URL for add/update")
    parser.add_argument("--add-aliases", default="", help="Comma-separated aliases")
    parser.add_argument("--add-description", default="", help="Description for add/update")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sites_path = Path(args.sites).resolve()

    if args.list:
        return cmd_list(sites_path)
    if args.add_name:
        if not args.add_url:
            print(json.dumps({"ok": False, "error": "--add-url is required with --add-name"}, ensure_ascii=False))
            return 1
        return cmd_add(args, sites_path)
    if args.query:
        return cmd_query(args, sites_path)

    print(
        json.dumps(
            {
                "ok": False,
                "error": "No action specified",
                "usage": "Use --query/--list/--add-name",
            },
            ensure_ascii=False,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
