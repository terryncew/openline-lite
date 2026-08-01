from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from common import SHELL_TIMEOUT_SECONDS
from trace_format import ToolObservation, relpath


@dataclass
class ToolResult:
    output: str
    observation: ToolObservation
    success: bool = True
    read_text: str | None = None
    read_relpath: str | None = None


def _safe_path(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError("path must be a non-empty string")
    p = (root / raw).resolve()
    rr = root.resolve()
    try:
        p.relative_to(rr)
    except ValueError:
        raise ValueError("path escapes repository root")
    return p


def _rel(root: Path, p: Path) -> str:
    return p.resolve().relative_to(root.resolve()).as_posix()


def _parse_patch_paths(patch: str) -> set[str]:
    out = set()
    for line in patch.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            token = line[4:].split("\t", 1)[0].strip()
            if token == "/dev/null":
                continue
            if token.startswith("a/") or token.startswith("b/"):
                token = token[2:]
            if token:
                out.add(token)
    return out


class ToolRuntime:
    def __init__(self, workspace: Path, shell_timeout: int = SHELL_TIMEOUT_SECONDS):
        self.workspace = workspace.resolve()
        self.shell_timeout = shell_timeout

    def execute(self, name: str, args: dict, *, max_seconds: float | None = None) -> ToolResult:
        if name == "read_file":
            return self.read_file(args)
        if name == "list_tree":
            return self.list_tree(args)
        if name == "search_text":
            return self.search_text(args)
        if name == "apply_patch":
            return self.apply_patch(args, max_seconds=max_seconds)
        if name == "run_shell":
            return self.run_shell(args, max_seconds=max_seconds)
        return ToolResult(f"ERROR: unsupported tool {name}", ToolObservation(name), False)

    def read_file(self, args: dict) -> ToolResult:
        raw = args.get("path")
        try:
            p = _safe_path(self.workspace, raw)
            if not p.is_file() or p.is_symlink():
                raise ValueError("target is not a regular file")
            data = p.read_bytes()
            text = data.decode("utf-8", "strict")
            rp = _rel(self.workspace, p)
            return ToolResult(text, ToolObservation("read_file", accessed_files={rp}), True, text, rp)
        except Exception as e:
            return ToolResult(f"ERROR: {type(e).__name__}: {e}", ToolObservation("read_file"), False)

    def list_tree(self, args: dict) -> ToolResult:
        raw = args.get("path", ".")
        max_entries = args.get("max_entries", 2000)
        try:
            max_entries = int(max_entries)
            if max_entries < 1 or max_entries > 10000:
                raise ValueError("max_entries outside [1,10000]")
            p = _safe_path(self.workspace, raw)
            if not p.exists() or not p.is_dir():
                raise ValueError("target is not a directory")
            rows = []
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d not in {".git", ".agent-home"})
                base = Path(dirpath)
                for name in sorted(filenames):
                    fp = base / name
                    if fp.is_symlink():
                        continue
                    rows.append(_rel(self.workspace, fp))
                    if len(rows) >= max_entries:
                        return ToolResult("\n".join(rows), ToolObservation("list_tree", listed_dirs={_rel(self.workspace, p) or "."}))
            return ToolResult("\n".join(rows), ToolObservation("list_tree", listed_dirs={_rel(self.workspace, p) or "."}))
        except Exception as e:
            return ToolResult(f"ERROR: {type(e).__name__}: {e}", ToolObservation("list_tree"), False)

    def search_text(self, args: dict) -> ToolResult:
        raw = args.get("path", ".")
        pattern = args.get("pattern")
        regex = bool(args.get("regex", False))
        max_matches = args.get("max_matches", 200)
        try:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("pattern must be non-empty string")
            max_matches = int(max_matches)
            if max_matches < 1 or max_matches > 1000:
                raise ValueError("max_matches outside [1,1000]")
            start = _safe_path(self.workspace, raw)
            files = [start] if start.is_file() else []
            if start.is_dir():
                for dirpath, dirnames, filenames in os.walk(start):
                    dirnames[:] = sorted(d for d in dirnames if d not in {".git", ".agent-home"})
                    base = Path(dirpath)
                    for name in sorted(filenames):
                        fp = base / name
                        if fp.is_file() and not fp.is_symlink():
                            files.append(fp)
            matcher = re.compile(pattern) if regex else None
            rows = []
            matched_files = set()
            for fp in files:
                try:
                    text = fp.read_text("utf-8", errors="strict")
                except (UnicodeDecodeError, OSError):
                    continue
                rp = _rel(self.workspace, fp)
                for lineno, line in enumerate(text.splitlines(), 1):
                    hit = bool(matcher.search(line)) if matcher else pattern in line
                    if hit:
                        matched_files.add(rp)
                        rows.append(f"{rp}:{lineno}:{line}")
                        if len(rows) >= max_matches:
                            return ToolResult("\n".join(rows), ToolObservation("search_text", searched_files=matched_files))
            return ToolResult("\n".join(rows), ToolObservation("search_text", searched_files=matched_files))
        except Exception as e:
            return ToolResult(f"ERROR: {type(e).__name__}: {e}", ToolObservation("search_text"), False)

    def apply_patch(self, args: dict, *, max_seconds: float | None = None) -> ToolResult:
        patch = args.get("patch")
        if not isinstance(patch, str) or not patch:
            return ToolResult("ERROR: patch must be a non-empty string", ToolObservation("apply_patch"), False)
        try:
            proc = subprocess.run(
                ["git", "apply", "--recount", "--whitespace=nowarn", "-"],
                cwd=self.workspace,
                input=patch,
                text=True,
                capture_output=True,
                timeout=max(0.001, min(float(self.shell_timeout), float(max_seconds))) if max_seconds is not None else self.shell_timeout,
                check=False,
                env=self._safe_env(),
            )
            paths = {p for p in _parse_patch_paths(patch) if not p.startswith(".git/")}
            obs = ToolObservation("apply_patch", accessed_files=paths)
            if proc.returncode != 0:
                return ToolResult(f"ERROR: git apply failed\n{proc.stderr}", obs, False)
            return ToolResult("PATCH_APPLIED", obs, True)
        except subprocess.TimeoutExpired:
            return ToolResult("ERROR: apply_patch timeout", ToolObservation("apply_patch"), False)
        except Exception as e:
            return ToolResult(f"ERROR: {type(e).__name__}: {e}", ToolObservation("apply_patch"), False)

    def _safe_env(self) -> dict[str, str]:
        # Deliberately excludes OPENAI_API_KEY, GITHUB_TOKEN, assignment data and runner secrets.
        keep = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "HOME": str(self.workspace / ".agent-home"),
            "PYTHONUNBUFFERED": "1",
        }
        Path(keep["HOME"]).mkdir(exist_ok=True)
        return keep

    def run_shell(self, args: dict, *, max_seconds: float | None = None) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, str) or not command:
            return ToolResult("ERROR: command must be a non-empty string", ToolObservation("run_shell"), False)
        # Orchestrator retains network access for OpenAI; only the model-invoked shell is isolated.
        # PID namespace hides host process namespaces; environment is stripped of runner/API secrets.
        wrapped = [
            "sudo",
            "unshare",
            "--net",
            "--pid",
            "--fork",
            "--mount-proc",
            "setpriv",
            f"--reuid={os.getuid()}",
            f"--regid={os.getgid()}",
            "--clear-groups",
            "bash",
            "-lc",
            command,
        ]
        try:
            proc = subprocess.run(
                wrapped,
                cwd=self.workspace,
                text=True,
                capture_output=True,
                timeout=max(0.001, min(float(self.shell_timeout), float(max_seconds))) if max_seconds is not None else self.shell_timeout,
                check=False,
                env=self._safe_env(),
            )
            output = f"exit_code={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            return ToolResult(output, ToolObservation("run_shell"), proc.returncode == 0)
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            stderr = e.stderr or ""
            return ToolResult(
                f"exit_code=124\nstdout:\n{stdout}\nstderr:\n{stderr}\nERROR: shell timeout after {self.shell_timeout}s",
                ToolObservation("run_shell"),
                False,
            )
        except Exception as e:
            return ToolResult(f"ERROR: {type(e).__name__}: {e}", ToolObservation("run_shell"), False)
