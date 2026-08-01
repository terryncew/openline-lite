from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from common import FORBIDDEN_EXPORT_KEYS


def _repo_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {".git", ".agent-home"})
        base = Path(dirpath)
        for name in sorted(filenames):
            p = base / name
            try:
                if p.is_file() and not p.is_symlink():
                    yield p
            except OSError:
                continue


def relpath(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def file_sha_value(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return "ABSENT"
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def content_snapshot(root: Path) -> dict[str, str]:
    out = {}
    for p in _repo_files(root):
        out[relpath(root, p)] = file_sha_value(p)
    return out


def changed_paths(before: dict[str, str], after: dict[str, str]) -> set[str]:
    keys = set(before) | set(after)
    return {k for k in keys if before.get(k) != after.get(k)}

def workspace_bytes_digest(root: Path) -> str:
    """Canonical digest of every regular workspace byte, including .git, before branch execution."""
    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(dirnames)
        base = Path(dirpath)
        for name in sorted(filenames):
            p = base / name
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            records.append((rel, h))
    data = "".join(f"{rel}\0{h}\n" for rel, h in records).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass
class ToolObservation:
    tool_name: str
    accessed_files: set[str] = field(default_factory=set)
    searched_files: set[str] = field(default_factory=set)
    listed_dirs: set[str] = field(default_factory=set)


class OperationalMapper:
    """
    Mechanical, label-blind realization of the frozen step observations.

    Step = one completed post-fork tool call.
    Tracked state fields = repository file objects referenced by read/search or changed by a tool.
    Canonical state value = sha256:<hex> of exact file bytes, or ABSENT.
    Dependency edges = accumulated typed trace edges between chronological tool-step IDs and
    repository file/directory/workspace objects. No semantic severity or condition input exists.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.previous_meta = content_snapshot(workspace)
        self.ever_existed = set(self.previous_meta)
        self.tracked_files: set[str] = set()
        self.edges: set[str] = set()
        self.step_index = 0

    def record_completed_tool(self, obs: ToolObservation) -> dict:
        self.step_index += 1
        after_meta = content_snapshot(self.workspace)
        mutations = changed_paths(self.previous_meta, after_meta)
        prior_existing = self.ever_existed.copy()
        write_events = len(mutations)
        revision_events = sum(1 for p in mutations if p in prior_existing)

        self.tracked_files.update(obs.accessed_files)
        self.tracked_files.update(obs.searched_files)
        self.tracked_files.update(mutations)

        sid = f"step:{self.step_index:04d}"
        for p in sorted(obs.accessed_files):
            self.edges.add(f"{sid}|reads|file:{p}")
        for p in sorted(obs.searched_files):
            self.edges.add(f"{sid}|searches|file:{p}")
        for d in sorted(obs.listed_dirs):
            self.edges.add(f"{sid}|lists|dir:{d}")
        if obs.tool_name == "run_shell":
            self.edges.add(f"{sid}|executes|workspace:.")
        for p in sorted(mutations):
            self.edges.add(f"{sid}|writes|file:{p}")

        state = {}
        for rel in sorted(self.tracked_files):
            p = self.workspace / rel
            state[f"file:{rel}"] = file_sha_value(p)

        self.ever_existed.update(after_meta)
        self.previous_meta = after_meta
        return {
            "index": self.step_index,
            "tool_name": obs.tool_name,
            "write_events": write_events,
            "revision_events": revision_events,
            "dependency_edges_after_step": sorted(self.edges),
            "state_fields_after_step": state,
        }


def assert_export_safe(obj):
    def walk(v, path="$"):
        if isinstance(v, dict):
            for k, val in v.items():
                lk = str(k).lower()
                if lk in FORBIDDEN_EXPORT_KEYS:
                    raise ValueError(f"forbidden export key at {path}.{k}")
                walk(val, f"{path}.{k}")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                walk(item, f"{path}[{i}]")
        elif isinstance(v, str):
            # Direct assignment labels are forbidden in scorer-visible trace values.
            if v in {"CLEAN", "PERTURBED"}:
                raise ValueError(f"forbidden assignment label at {path}")
    walk(obj)
