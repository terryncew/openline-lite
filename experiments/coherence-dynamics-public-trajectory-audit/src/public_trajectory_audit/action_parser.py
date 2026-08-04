from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("submit", re.compile(r"^(?:submit|submit_patch|finalize)\b", re.I)),
    ("verify", re.compile(r"^(?:pytest|python\s+-m\s+(?:pytest|unittest)|npm\s+(?:test|run\s+test)|cargo\s+test|go\s+test|make\s+test|tox\b|test\b)", re.I)),
    ("edit", re.compile(r"^(?:edit|create|write|apply_patch|patch|sed\s+-i|perl\s+-pi|cat\s+>|tee\b)", re.I)),
    ("read", re.compile(r"^(?:open|view|cat\b|head\b|tail\b|sed\s+-n|less\b|more\b)", re.I)),
    ("search", re.compile(r"^(?:search|search_dir|search_file|find_file|find\b|grep\b|rg\b|ls\b|tree\b)", re.I)),
    ("exec", re.compile(r"^(?:python\b|bash\b|sh\b|git\b|npm\b|pnpm\b|yarn\b|cargo\b|go\b|make\b|cd\b|pwd\b)", re.I)),
)

_PATH_RE = re.compile(r"(?<![\w.-])(?:\.?\.?/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|\b[A-Za-z0-9_.-]+\.(?:py|js|ts|tsx|jsx|json|toml|yaml|yml|md|rs|go|java|c|cc|cpp|h|hpp|sh)\b")
_ERROR_RE = re.compile(r"(?:\btraceback\b|\berror\b|\bexception\b|command exited with code [1-9]|exit code [1-9]|tests? failed|FAILED(?:\s|$)|no such file|not found)", re.I)


@dataclass(frozen=True)
class ParsedAction:
    category: str
    command: str
    command_hash: str
    targets: tuple[str, ...]
    parse_confidence: str


def _candidate_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    explicit: list[str] = []
    for line in lines:
        cleaned = line.strip("`$> ")
        if any(pattern.search(cleaned) for _, pattern in _ACTION_PATTERNS):
            explicit.append(cleaned)
    return explicit


def parse_action(text: str) -> ParsedAction:
    candidates = _candidate_lines(text)
    if not candidates:
        command = ""
        return ParsedAction("other", command, hashlib.sha256(b"").hexdigest(), (), "unavailable")
    command = candidates[-1]
    category = "other"
    for label, pattern in _ACTION_PATTERNS:
        if pattern.search(command):
            category = label
            break
    targets = tuple(sorted(set(_PATH_RE.findall(command))))
    digest = hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()
    return ParsedAction(category, command, digest, targets, "explicit_command_lexical")


def observation_is_error(text: str) -> bool:
    return bool(_ERROR_RE.search(text))
