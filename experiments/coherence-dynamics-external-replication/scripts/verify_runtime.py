from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify(lock_path: Path) -> dict:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    observed_python = platform.python_version()
    if platform.python_implementation() != lock["python"]["implementation"]:
        problems.append(f"python implementation {platform.python_implementation()} != {lock['python']['implementation']}")
    if observed_python != lock["python"]["version"]:
        problems.append(f"python version {observed_python} != {lock['python']['version']}")
    machine = platform.machine().lower()
    expected_machine = lock["python"]["architecture"].lower()
    aliases = {"amd64": "x86_64", "x64": "x86_64"}
    if aliases.get(machine, machine) != aliases.get(expected_machine, expected_machine):
        problems.append(f"architecture {machine} != {expected_machine}")
    observed_packages = {}
    for package, expected in lock["packages"].items():
        try:
            observed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"missing package {package}=={expected}")
            continue
        observed_packages[package] = observed
        if observed != expected:
            problems.append(f"package {package} {observed} != {expected}")
    freeze_path = ROOT / "ORIGINAL_AUDIT_PIP_FREEZE.txt"
    if sha256(freeze_path) != lock["source_pip_freeze_sha256"]:
        problems.append("original audit pip freeze hash mismatch")
    receipt = {
        "schema": "coherence-dynamics.external-replication.runtime-verification.v1",
        "status": "PASS" if not problems else "FAIL",
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": observed_packages,
        "runtime_lock_sha256": sha256(lock_path),
        "original_audit_pip_freeze_sha256": sha256(freeze_path),
        "problems": problems,
    }
    return receipt

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", default="RUNTIME_LOCK.json")
    parser.add_argument("--output")
    args = parser.parse_args()
    receipt = verify(Path(args.lock))
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text, end="")
    if receipt["status"] != "PASS":
        raise SystemExit("runtime does not match frozen original audit environment")

if __name__ == "__main__":
    main()
