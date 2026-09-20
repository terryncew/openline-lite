"""CLI for living-wiki source standing.

Commands:
  openline-wiki init [--source-dir DIR] [--wiki-dir DIR] [--root DIR] [--force]
  openline-wiki record PAGE --source SRC ... [--complete | --incomplete] [--root DIR]
  openline-wiki scan [--root DIR] [--json]
  openline-wiki context [--root DIR]

Determines whether a compiled wiki page still has the source standing it was
recorded against. It does not determine whether a wiki claim is true.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .canonical import pretty
from .wiki import (
    COMPLETE,
    INCOMPLETE,
    REOPEN,
    RETAIN,
    UNDETERMINED,
    PageStanding,
    context_document,
    init_wiki,
    load_config,
    record_page,
    scan_document,
    scan_wiki,
)


def _card(config: Any, standings: Sequence[PageStanding]) -> str:
    reopen = [s for s in standings if s.disposition == REOPEN]
    retain = [s for s in standings if s.disposition == RETAIN]
    undetermined = [s for s in standings if s.disposition == UNDETERMINED]
    lines = [
        "OPENLINE WIKI STANDING",
        "",
        f"source: {config.source_dir}   wiki: {config.wiki_dir}",
        "",
        f"REOPEN: {len(reopen)}",
        f"RETAIN: {len(retain)}",
        f"UNDETERMINED: {len(undetermined)}",
        "",
    ]
    for standing in sorted(standings, key=lambda s: (s.disposition, s.page)):
        detail = " (" + ", ".join(standing.reasons) + ")" if standing.reasons else ""
        lines.append(f"{standing.disposition}  {standing.page}{detail}")
    lines.extend(
        [
            "",
            "Boundary: a compiled page is flagged for reconsideration when its",
            "recorded sources changed or disappeared. This says nothing about",
            "whether any wiki claim is true.",
        ]
    )
    return "\n".join(lines) + "\n"


def _cmd_init(args: argparse.Namespace) -> int:
    config = init_wiki(
        Path(args.root), args.source_dir, args.wiki_dir, force=args.force
    )
    print(
        pretty(
            {
                "initialized": True,
                "root": str(config.root),
                "source_dir": config.source_dir,
                "wiki_dir": config.wiki_dir,
            }
        )
    )
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root))
    completeness = COMPLETE if args.complete else INCOMPLETE
    record = record_page(config, args.page, args.source or [], completeness)
    print(
        pretty(
            {
                "recorded": True,
                "page": record.page,
                "completeness": record.completeness,
                "sources": [path for path, _ in record.sources],
                "record_sha256": record.record_sha256,
            }
        )
    )
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root))
    standings = scan_wiki(config)
    if args.json:
        print(pretty(scan_document(config, standings)))
    else:
        print(_card(config, standings), end="")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root))
    standings = scan_wiki(config)
    print(pretty(context_document(config, standings)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openline-wiki",
        description=(
            "Know which compiled wiki pages need reconsideration when "
            "their source evidence changes."
        ),
    )
    def _add_root(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", default=".", help="wiki project root (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="initialize wiki standing state")
    _add_root(init_p)
    init_p.add_argument("--source-dir", default="raw")
    init_p.add_argument("--wiki-dir", default="wiki")
    init_p.add_argument("--force", action="store_true")
    init_p.set_defaults(handler=_cmd_init)

    record_p = sub.add_parser("record", help="freeze a page dependency record")
    _add_root(record_p)
    record_p.add_argument("page", help="wiki page path relative to the wiki dir")
    record_p.add_argument(
        "--source",
        action="append",
        default=[],
        help="source path relative to the source dir (repeatable)",
    )
    group = record_p.add_mutually_exclusive_group()
    group.add_argument("--complete", action="store_true")
    group.add_argument("--incomplete", action="store_true", default=True)
    record_p.set_defaults(handler=_cmd_record)

    scan_p = sub.add_parser("scan", help="partition pages by source standing")
    _add_root(scan_p)
    scan_p.add_argument("--json", action="store_true")
    scan_p.set_defaults(handler=_cmd_scan)

    context_p = sub.add_parser(
        "context",
        help="machine-readable standing summary for agent context",
    )
    _add_root(context_p)
    context_p.set_defaults(handler=_cmd_context)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.handler(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
