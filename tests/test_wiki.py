"""Tests for openline-wiki (living-wiki source standing).

Covers the frozen three-phase fixture, every hostile/integrity case from the
work order, and the non-mutation guarantee. Fail-closed dispositions are
asserted explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from openline_lite.canonical import loads, sha256_hex
from openline_lite.wiki import (
    COMPLETE,
    INCOMPLETE,
    REOPEN,
    RETAIN,
    UNDETERMINED,
    init_wiki,
    load_config,
    page_record_id,
    record_page,
    resolve_within,
    scan_wiki,
    context_document,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WikiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "raw").mkdir()
        (self.root / "wiki").mkdir()
        self.config = init_wiki(self.root, "raw", "wiki")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------

    def write_source(self, name: str, content: bytes) -> None:
        (self.root / "raw" / name).write_bytes(content)

    def write_page(self, name: str, content: bytes) -> None:
        (self.root / "wiki" / name).write_bytes(content)

    def record(self, page: str, sources: list[str], complete: bool = True):
        return record_page(
            self.config,
            page,
            sources,
            COMPLETE if complete else INCOMPLETE,
        )

    def scan_map(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        return {
            standing.page: (standing.disposition, standing.reasons)
            for standing in scan_wiki(self.config)
        }

    # -- frozen three-phase fixture ------------------------------------

    def test_three_phase_fixture(self) -> None:
        self.write_source("source-a.md", b"source a v1")
        self.write_source("source-b.md", b"source b v1")
        self.write_page("topic-a.md", b"topic a compiled")
        self.write_page("topic-b.md", b"topic b compiled")
        self.write_page("unknown.md", b"unknown compiled")

        self.record("topic-a.md", ["source-a.md"])
        self.record("topic-b.md", ["source-b.md"])
        self.record("unknown.md", ["source-a.md"], complete=False)

        result = self.scan_map()
        self.assertEqual(result["topic-a.md"][0], RETAIN)
        self.assertEqual(result["topic-b.md"][0], RETAIN)
        self.assertEqual(result["unknown.md"], (UNDETERMINED, ("incomplete_capture",)))

        # Phase 2: change source-a.
        self.write_source("source-a.md", b"source a v2 CHANGED")
        result = self.scan_map()
        self.assertEqual(result["topic-a.md"][0], REOPEN)
        self.assertIn("source_changed:source-a.md", result["topic-a.md"][1])
        self.assertEqual(result["topic-b.md"][0], RETAIN)
        self.assertEqual(result["unknown.md"][0], UNDETERMINED)

        # Phase 3: delete source-b.
        os.remove(self.root / "raw" / "source-b.md")
        result = self.scan_map()
        self.assertEqual(result["topic-a.md"][0], REOPEN)
        self.assertEqual(result["topic-b.md"][0], REOPEN)
        self.assertIn("source_missing:source-b.md", result["topic-b.md"][1])
        self.assertEqual(result["unknown.md"][0], UNDETERMINED)

    # -- scan never mutates --------------------------------------------

    def test_scan_does_not_mutate_wiki(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])

        before: dict[Path, bytes] = {}
        for path in list((self.root / "raw").rglob("*")) + list(
            (self.root / "wiki").rglob("*")
        ):
            if path.is_file():
                before[path] = path.read_bytes()

        scan_wiki(self.config)

        for path, content in before.items():
            self.assertTrue(path.exists(), f"scan deleted {path}")
            self.assertEqual(path.read_bytes(), content, f"scan modified {path}")

    def test_reopen_not_cleared_by_rescan(self) -> None:
        self.write_source("source-a.md", b"v1")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])
        self.write_source("source-a.md", b"v2")
        first = self.scan_map()["topic-a.md"][0]
        second = self.scan_map()["topic-a.md"][0]
        self.assertEqual(first, REOPEN)
        self.assertEqual(second, REOPEN)

    def test_rerecord_returns_to_retain(self) -> None:
        self.write_source("source-a.md", b"v1")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])
        self.write_source("source-a.md", b"v2")
        self.assertEqual(self.scan_map()["topic-a.md"][0], REOPEN)
        # Recompile the page against the new source state, then record again.
        self.write_page("topic-a.md", b"page recompiled against v2")
        self.record("topic-a.md", ["source-a.md"])
        self.assertEqual(self.scan_map()["topic-a.md"][0], RETAIN)

    # -- missingness -----------------------------------------------------

    def test_page_without_record_is_undetermined(self) -> None:
        self.write_page("orphan.md", b"no record")
        self.assertEqual(
            self.scan_map()["orphan.md"], (UNDETERMINED, ("no_record",))
        )

    def test_malformed_sidecar_is_undetermined(self) -> None:
        self.write_page("bad.md", b"page")
        sidecar = self.config.pages_dir() / page_record_id("bad.md")
        sidecar.write_bytes(b"this is not json {{{")
        self.assertEqual(
            self.scan_map()["bad.md"], (UNDETERMINED, ("malformed_record",))
        )

    def test_tampered_sidecar_hash_is_undetermined(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])
        sidecar = self.config.pages_dir() / page_record_id("topic-a.md")
        record = loads(sidecar.read_bytes())
        record["sources"][0]["sha256"] = "ff" * 32
        sidecar.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(
            self.scan_map()["topic-a.md"], (UNDETERMINED, ("malformed_record",))
        )

    def test_incomplete_capture_is_undetermined(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"], complete=False)
        self.assertEqual(
            self.scan_map()["topic-a.md"], (UNDETERMINED, ("incomplete_capture",))
        )

    def test_page_modified_after_record_is_undetermined(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page v1")
        self.record("topic-a.md", ["source-a.md"])
        self.write_page("topic-a.md", b"page v2, no new record")
        self.assertEqual(
            self.scan_map()["topic-a.md"],
            (UNDETERMINED, ("page_modified_after_record",)),
        )

    # -- record-time validation ------------------------------------------

    def test_record_refuses_duplicate_source(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        with self.assertRaisesRegex(ValueError, "source_duplicate"):
            self.record("topic-a.md", ["source-a.md", "source-a.md"])

    def test_reordered_sources_are_equivalent(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_source("source-b.md", b"b")
        self.write_page("topic-a.md", b"page")
        first = self.record("topic-a.md", ["source-a.md", "source-b.md"])
        second = self.record("topic-a.md", ["source-b.md", "source-a.md"])
        self.assertEqual(first.record_sha256, second.record_sha256)
        self.assertEqual(self.scan_map()["topic-a.md"][0], RETAIN)

    def test_record_complete_with_no_sources_refused(self) -> None:
        self.write_page("topic-a.md", b"page")
        with self.assertRaisesRegex(ValueError, "complete_with_no_sources"):
            self.record("topic-a.md", [])

    def test_record_incomplete_with_no_sources_scans_undetermined(self) -> None:
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", [], complete=False)
        self.assertEqual(
            self.scan_map()["topic-a.md"], (UNDETERMINED, ("incomplete_capture",))
        )

    def test_record_missing_source_refused(self) -> None:
        self.write_page("topic-a.md", b"page")
        with self.assertRaisesRegex(ValueError, "source_not_found"):
            self.record("topic-a.md", ["does-not-exist.md"])

    # -- path hostility ----------------------------------------------------

    def test_path_traversal_refused(self) -> None:
        self.write_page("topic-a.md", b"page")
        with self.assertRaisesRegex(ValueError, "path_invalid"):
            self.record("topic-a.md", ["../outside.md"])
        with self.assertRaisesRegex(ValueError, "path_invalid"):
            resolve_within(self.root, "..", "page")

    def test_absolute_path_refused(self) -> None:
        self.write_page("topic-a.md", b"page")
        with self.assertRaisesRegex(ValueError, "path_absolute"):
            self.record("topic-a.md", ["/etc/passwd"])
        with self.assertRaisesRegex(ValueError, "path_absolute"):
            resolve_within(self.root, "/etc/passwd", "page")

    def test_windows_absolute_path_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "path_absolute"):
            resolve_within(self.root, "C:\\Windows\\x", "page")

    def test_symlink_escape_refused_at_record_time(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_bytes(b"secret")
        self.write_page("topic-a.md", b"page")
        (self.root / "raw" / "link.md").symlink_to(outside / "secret.md")
        with self.assertRaisesRegex(ValueError, "source_path_escape"):
            self.record("topic-a.md", ["link.md"])

    def test_swapped_symlink_escape_is_reopen(self) -> None:
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "evil.md").write_bytes(b"evil")
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])
        # An attacker replaces the recorded source with a symlink escaping
        # the source dir. Scan must not follow it: fail closed.
        os.remove(self.root / "raw" / "source-a.md")
        (self.root / "raw" / "source-a.md").symlink_to(outside / "evil.md")
        disposition, reasons = self.scan_map()["topic-a.md"]
        self.assertEqual(disposition, REOPEN)
        self.assertTrue(
            any(
                r.startswith("source_missing:")
                or r.startswith("source_unresolvable:")
                or r.startswith("source_unreadable:")
                for r in reasons
            ),
            reasons,
        )

    def test_renamed_source_is_reopen(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])
        os.rename(self.root / "raw" / "source-a.md", self.root / "raw" / "source-a-renamed.md")
        disposition, reasons = self.scan_map()["topic-a.md"]
        self.assertEqual(disposition, REOPEN)
        self.assertIn("source_missing:source-a.md", reasons)

    # -- determinism ---------------------------------------------------------

    def test_utf8_and_newline_determinism(self) -> None:
        self.write_source("uni.md", "héllo wörld\nline2".encode("utf-8"))
        self.write_source("crlf.md", b"a\r\nb\r\n")
        self.write_source("lf.md", b"a\nb\n")
        self.write_page("topic-a.md", b"page")
        first = self.record("topic-a.md", ["uni.md", "crlf.md"])
        second = self.record("topic-a.md", ["uni.md", "crlf.md"])
        self.assertEqual(first.record_sha256, second.record_sha256)
        # CRLF and LF are distinct byte sequences: distinct standing.
        self.assertNotEqual(
            sha256_hex(b"a\r\nb\r\n"), sha256_hex(b"a\nb\n")
        )
        # Same bytes on disk hash to the recorded digest (sources sorted by path).
        by_path = {path: digest for path, digest in first.sources}
        self.assertEqual(by_path["crlf.md"], sha256_hex(b"a\r\nb\r\n"))
        self.assertEqual(
            by_path["uni.md"], sha256_hex("héllo wörld\nline2".encode("utf-8"))
        )

    def test_context_is_deterministic(self) -> None:
        self.write_source("source-a.md", b"a")
        self.write_page("topic-a.md", b"page")
        self.record("topic-a.md", ["source-a.md"])
        standings = scan_wiki(self.config)
        first = context_document(self.config, standings)
        second = context_document(self.config, scan_wiki(self.config))
        self.assertEqual(first, second)

    # -- config / init ---------------------------------------------------------

    def test_init_is_refused_when_already_initialized(self) -> None:
        with self.assertRaisesRegex(ValueError, "wiki_already_initialized"):
            init_wiki(self.root, "raw", "wiki")

    def test_init_refuses_absolute_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaisesRegex(ValueError, "absolute"):
                init_wiki(Path(other), "/raw", "wiki")

    def test_load_config_rejects_tampered_config(self) -> None:
        config_path = self.root / ".openline" / "wiki" / "config.json"
        value = loads(config_path.read_bytes())
        value["source_dir"] = "other"
        config_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "config_hash_mismatch"):
            load_config(self.root)

    def test_record_id_is_stable_and_path_free(self) -> None:
        first = page_record_id("nested/deep/topic.md")
        second = page_record_id("nested/deep/topic.md")
        self.assertEqual(first, second)
        self.assertNotIn("/", first)
        self.assertNotIn("\\", first)
        self.assertNotEqual(first, page_record_id("nested/deep/other.md"))


if __name__ == "__main__":
    unittest.main()
