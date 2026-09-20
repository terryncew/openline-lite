"""Living-wiki standing: does a compiled wiki page still have the source
standing it was recorded against?

This module packages the already-earned OpenLine Lite standing semantics
(TRUST_BOUNDARY.md: REOPEN / RETAIN / UNDETERMINED) for LLM-maintained
living wikis with a ``raw/`` + ``wiki/`` source/compiled separation.

It does NOT determine whether a wiki claim is true. It determines whether a
previously compiled wiki artifact still has the source standing it was
recorded against. A source remaining byte-identical does not make the source
true; a source changing does not prove the new version is better.

The result is about reconsideration, not truth.

Dependencies enter through an explicit deterministic ``record`` interface.
OpenLine does not discover semantic dependencies. A record binds:

* the wiki page's relative path and content hash at recording time;
* each declared source's relative path and exact content hash;
* a completeness declaration (``complete`` / ``incomplete``).

``complete`` means the workflow explicitly treated the supplied dependency
set as complete. It does not mean the sources were independently verified
or that every material influence was captured. An agent-written dependency
list is a declaration about representation, not an independently verified
provenance claim.

``scan`` partitions compiled pages into REOPEN / RETAIN / UNDETERMINED:

* REOPEN: a frozen source dependency no longer has the exact recorded
  standing (bytes changed, source removed, source unresolvable).
* RETAIN: all frozen source dependencies remain exactly as recorded AND
  the dependency declaration was explicitly marked complete.
* UNDETERMINED: the system cannot establish that the page's dependency
  declaration is complete (no record, incomplete capture, malformed
  sidecar, page modified after its record). Missingness never becomes
  RETAIN.

``policy_authority: receiver_owned``
``runtime_permission: NONE``
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .canonical import loads, object_hash, pretty, sha256_hex

WIKI_RECORD_SCHEMA = "openline.wiki-dependency.v1"
WIKI_CONFIG_SCHEMA = "openline.wiki-config.v1"
WIKI_SCAN_SCHEMA = "openline.wiki-scan.v1"
WIKI_CONTEXT_SCHEMA = "openline.wiki-context.v1"

COMPLETE = "complete"
INCOMPLETE = "incomplete"

REOPEN = "REOPEN"
RETAIN = "RETAIN"
UNDETERMINED = "UNDETERMINED"

MAX_FILE_BYTES = 32 * 1_048_576
MAX_RECORD_BYTES = 1_048_576
MAX_SOURCES_PER_PAGE = 256
MAX_PAGES = 16_384

OPENLINE_DIR = ".openline"
WIKI_STATE_DIR = "wiki"
CONFIG_NAME = "config.json"
PAGES_DIR = "pages"

PAGE_EXTENSIONS = (".md", ".markdown")


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"{label}_unreadable") from exc
    if size > MAX_FILE_BYTES:
        raise ValueError(f"{label}_size_limit_exceeded")
    return path.read_bytes()


def _relative_posix(base: Path, target: Path) -> str:
    return target.relative_to(base).as_posix()


def resolve_within(root: Path, relative: str, label: str) -> Path:
    """Resolve a user-supplied relative path, contained strictly within root.

    Rejects absolute paths, ``..`` segments, and symlink escapes. Raises
    ValueError on any violation (fail closed).
    """
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label}_path_invalid")
    if os.path.isabs(relative):
        raise ValueError(f"{label}_path_absolute")
    if re.match(r"^[A-Za-z]:", relative):
        raise ValueError(f"{label}_path_absolute")
    parts = relative.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label}_path_invalid")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label}_path_escape") from exc
    return candidate


def page_record_id(page_relpath: str) -> str:
    """Stable deterministic sidecar filename for a wiki page path."""
    return sha256_hex(page_relpath.encode("utf-8"))[:16] + ".json"


@dataclass(frozen=True)
class WikiConfig:
    root: Path
    source_dir: str
    wiki_dir: str
    config_path: Path

    def source_root(self) -> Path:
        return self.root / self.source_dir

    def wiki_root(self) -> Path:
        return self.root / self.wiki_dir

    def pages_dir(self) -> Path:
        return self.config_path.parent / PAGES_DIR

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WIKI_CONFIG_SCHEMA,
            "version": 1,
            "source_dir": self.source_dir,
            "wiki_dir": self.wiki_dir,
        }


def _validate_dir_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}_invalid")
    if os.path.isabs(value) or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"{label}_absolute")
    parts = value.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label}_invalid")
    return value


def init_wiki(root: Path, source_dir: str, wiki_dir: str, *, force: bool = False) -> WikiConfig:
    """Create the .openline/wiki state directory. Never touches raw/ or wiki/."""
    root = root.resolve()
    source_dir = _validate_dir_name(source_dir, "source_dir")
    wiki_dir = _validate_dir_name(wiki_dir, "wiki_dir")
    state_dir = root / OPENLINE_DIR / WIKI_STATE_DIR
    config_path = state_dir / CONFIG_NAME
    if config_path.exists() and not force:
        raise ValueError("wiki_already_initialized")
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / PAGES_DIR).mkdir(parents=True, exist_ok=True)
    config = WikiConfig(root=root, source_dir=source_dir, wiki_dir=wiki_dir, config_path=config_path)
    body = config.to_dict()
    body["config_sha256"] = object_hash(body)
    config_path.write_text(pretty(body), encoding="utf-8")
    return config


def load_config(root: Path) -> WikiConfig:
    """Load the wiki config. Fails closed on any malformed state."""
    root = root.resolve()
    config_path = root / OPENLINE_DIR / WIKI_STATE_DIR / CONFIG_NAME
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise ValueError("wiki_not_initialized") from exc
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("config_size_limit_exceeded")
    value = loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("config_malformed")
    allowed = {"schema", "version", "source_dir", "wiki_dir", "config_sha256"}
    if set(value) != allowed:
        raise ValueError("config_malformed")
    if value.get("schema") != WIKI_CONFIG_SCHEMA or value.get("version") != 1:
        raise ValueError("config_schema_unsupported")
    source_dir = _validate_dir_name(value.get("source_dir"), "source_dir")
    wiki_dir = _validate_dir_name(value.get("wiki_dir"), "wiki_dir")
    expected = value.get("config_sha256")
    body = {key: value[key] for key in ("schema", "version", "source_dir", "wiki_dir")}
    if not _is_sha256(expected) or object_hash(body) != expected:
        raise ValueError("config_hash_mismatch")
    return WikiConfig(root=root, source_dir=source_dir, wiki_dir=wiki_dir, config_path=config_path)


@dataclass(frozen=True)
class PageRecord:
    page: str
    page_sha256: str
    sources: tuple[tuple[str, str], ...]
    completeness: str
    record_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": WIKI_RECORD_SCHEMA,
            "version": 1,
            "page": self.page,
            "page_sha256": self.page_sha256,
            "sources": [
                {"path": path, "sha256": digest} for path, digest in self.sources
            ],
            "completeness": self.completeness,
            "record_sha256": self.record_sha256,
        }


def _record_body(page: str, page_sha256: str, sources: tuple[tuple[str, str], ...], completeness: str) -> dict[str, Any]:
    return {
        "schema": WIKI_RECORD_SCHEMA,
        "version": 1,
        "page": page,
        "page_sha256": page_sha256,
        "sources": [{"path": path, "sha256": digest} for path, digest in sources],
        "completeness": completeness,
    }


def _parse_record(raw: bytes, expected_page: str) -> PageRecord:
    value = loads(raw)
    if not isinstance(value, Mapping):
        raise ValueError("record_malformed")
    allowed = {"schema", "version", "page", "page_sha256", "sources", "completeness", "record_sha256"}
    if set(value) != allowed:
        raise ValueError("record_malformed")
    if value.get("schema") != WIKI_RECORD_SCHEMA or value.get("version") != 1:
        raise ValueError("record_schema_unsupported")
    page = value.get("page")
    if page != expected_page or not isinstance(page, str) or not page:
        raise ValueError("record_page_mismatch")
    page_sha256 = value.get("page_sha256")
    if not _is_sha256(page_sha256):
        raise ValueError("record_page_hash_invalid")
    completeness = value.get("completeness")
    if completeness not in {COMPLETE, INCOMPLETE}:
        raise ValueError("record_completeness_invalid")
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES_PER_PAGE:
        raise ValueError("record_sources_invalid")
    sources: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw_sources:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ValueError("record_sources_invalid")
        path, digest = item.get("path"), item.get("sha256")
        if not isinstance(path, str) or not path or not _is_sha256(digest):
            raise ValueError("record_sources_invalid")
        if path in seen:
            raise ValueError("record_source_duplicate")
        seen.add(path)
        sources.append((path, digest))
    body = _record_body(page, page_sha256, tuple(sources), completeness)
    expected_hash = value.get("record_sha256")
    if not _is_sha256(expected_hash) or object_hash(body) != expected_hash:
        raise ValueError("record_hash_mismatch")
    return PageRecord(
        page=page,
        page_sha256=page_sha256,
        sources=tuple(sources),
        completeness=completeness,
        record_sha256=expected_hash,
    )


def record_page(
    config: WikiConfig,
    page: str,
    sources: Sequence[str],
    completeness: str,
) -> PageRecord:
    """Freeze a dependency record for one wiki page.

    Sources must exist and resolve within the source dir at record time;
    the page must exist within the wiki dir. Completeness defaults to
    ``incomplete`` at the CLI layer; recording ``complete`` with an empty
    source set is refused (fail closed).
    """
    if completeness not in {COMPLETE, INCOMPLETE}:
        raise ValueError("completeness_invalid")
    if completeness == COMPLETE and not sources:
        raise ValueError("complete_with_no_sources")
    page_path = resolve_within(config.wiki_root(), page, "page")
    page_rel = _relative_posix(config.wiki_root(), page_path)
    page_bytes = _read_bytes(page_path, "page")
    page_sha = sha256_hex(page_bytes)

    bound: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, str):
            raise ValueError("source_path_invalid")
        source_path = resolve_within(config.source_root(), source, "source")
        source_rel = _relative_posix(config.source_root(), source_path)
        if source_rel in seen:
            raise ValueError("source_duplicate")
        seen.add(source_rel)
        try:
            source_bytes = _read_bytes(source_path, "source")
        except ValueError as exc:
            if str(exc).startswith("source_unreadable"):
                raise ValueError(f"source_not_found:{source_rel}") from exc
            raise
        bound.append((source_rel, sha256_hex(source_bytes)))
    bound_tuple = tuple(sorted(bound))

    body = _record_body(page_rel, page_sha, bound_tuple, completeness)
    record = PageRecord(
        page=page_rel,
        page_sha256=page_sha,
        sources=bound_tuple,
        completeness=completeness,
        record_sha256=object_hash(body),
    )
    sidecar = config.pages_dir() / page_record_id(page_rel)
    sidecar.write_text(pretty(record.to_dict()), encoding="utf-8")
    return record


@dataclass(frozen=True)
class PageStanding:
    page: str
    disposition: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
        }


def _read_record_file(path: Path, expected_page: str) -> PageRecord:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("record_unreadable") from exc
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("record_size_limit_exceeded")
    return _parse_record(raw, expected_page)


def _scan_one(config: WikiConfig, page_rel: str, page_abs: Path) -> PageStanding:
    sidecar = config.pages_dir() / page_record_id(page_rel)
    if not sidecar.exists():
        return PageStanding(page=page_rel, disposition=UNDETERMINED, reasons=("no_record",))
    try:
        record = _read_record_file(sidecar, page_rel)
    except ValueError:
        return PageStanding(page=page_rel, disposition=UNDETERMINED, reasons=("malformed_record",))

    current_page_sha = sha256_hex(_read_bytes(page_abs, "page"))
    if current_page_sha != record.page_sha256:
        # The record describes the compiled page at record time. If the
        # page itself changed without a deliberate new record, the
        # declaration cannot be trusted. This is missingness-like, not
        # source-loss-like, so it is UNDETERMINED (fail closed), not REOPEN.
        return PageStanding(
            page=page_rel, disposition=UNDETERMINED, reasons=("page_modified_after_record",)
        )
    if record.completeness != COMPLETE:
        return PageStanding(page=page_rel, disposition=UNDETERMINED, reasons=("incomplete_capture",))
    if not record.sources:
        return PageStanding(page=page_rel, disposition=UNDETERMINED, reasons=("empty_source_set",))

    reopen_reasons: list[str] = []
    for source_rel, recorded_sha in record.sources:
        try:
            source_path = resolve_within(config.source_root(), source_rel, "source")
        except ValueError:
            reopen_reasons.append(f"source_unresolvable:{source_rel}")
            continue
        if not source_path.is_file():
            reopen_reasons.append(f"source_missing:{source_rel}")
            continue
        try:
            current_sha = sha256_hex(_read_bytes(source_path, "source"))
        except ValueError:
            reopen_reasons.append(f"source_unreadable:{source_rel}")
            continue
        if current_sha != recorded_sha:
            reopen_reasons.append(f"source_changed:{source_rel}")

    if reopen_reasons:
        return PageStanding(page=page_rel, disposition=REOPEN, reasons=tuple(sorted(reopen_reasons)))
    return PageStanding(page=page_rel, disposition=RETAIN, reasons=())


def iter_wiki_pages(config: WikiConfig) -> list[tuple[str, Path]]:
    wiki_root = config.wiki_root()
    if not wiki_root.is_dir():
        raise ValueError("wiki_dir_missing")
    found: list[tuple[str, Path]] = []
    for candidate in sorted(wiki_root.rglob("*")):
        if candidate.is_file() and candidate.suffix.lower() in PAGE_EXTENSIONS:
            found.append((_relative_posix(wiki_root, candidate), candidate))
    if len(found) > MAX_PAGES:
        raise ValueError("page_limit_exceeded")
    return found


def scan_wiki(config: WikiConfig) -> tuple[PageStanding, ...]:
    """Partition every compiled wiki page. Reads only; never mutates raw/ or wiki/."""
    return tuple(_scan_one(config, rel, abs_path) for rel, abs_path in iter_wiki_pages(config))


def scan_document(config: WikiConfig, standings: Sequence[PageStanding]) -> dict[str, Any]:
    body = {
        "schema": WIKI_SCAN_SCHEMA,
        "source_dir": config.source_dir,
        "wiki_dir": config.wiki_dir,
        "reopen": [s.to_dict() for s in sorted(standings, key=lambda s: s.page) if s.disposition == REOPEN],
        "retain": [s.to_dict() for s in sorted(standings, key=lambda s: s.page) if s.disposition == RETAIN],
        "undetermined": [s.to_dict() for s in sorted(standings, key=lambda s: s.page) if s.disposition == UNDETERMINED],
        "policy_authority": "receiver_owned",
        "runtime_permission": "NONE",
    }
    body["scan_sha256"] = object_hash(body)
    return body


def context_document(config: WikiConfig, standings: Sequence[PageStanding]) -> dict[str, Any]:
    """Deterministic machine-readable standing summary for agent context.

    Not retrieval: a standing partition only. Sorted, no timestamps.
    """
    body = {
        "schema": WIKI_CONTEXT_SCHEMA,
        "source_dir": config.source_dir,
        "wiki_dir": config.wiki_dir,
        "retain": sorted(s.page for s in standings if s.disposition == RETAIN),
        "reopen": [s.to_dict() for s in sorted(standings, key=lambda s: s.page) if s.disposition == REOPEN],
        "undetermined": [s.to_dict() for s in sorted(standings, key=lambda s: s.page) if s.disposition == UNDETERMINED],
        "policy_authority": "receiver_owned",
        "runtime_permission": "NONE",
    }
    body["context_sha256"] = object_hash(body)
    return body
