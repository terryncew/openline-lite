from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluate import run_external
from .prepare import prepare_external, prepare_source
from .profile import recover_source_profile


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    command = subparsers.add_parser("prepare-source")
    command.add_argument("--input", action="append", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--lock", required=True)

    command = subparsers.add_parser("recover-source")
    command.add_argument("--prepared", required=True)
    command.add_argument("--lock", required=True)
    command.add_argument("--recovery", required=True)
    command.add_argument("--output", required=True)

    command = subparsers.add_parser("prepare-external")
    command.add_argument("--input", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--protocol", required=True)

    command = subparsers.add_parser("run-external")
    command.add_argument("--prepared", required=True)
    command.add_argument("--profile", required=True)
    command.add_argument("--protocol", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--bootstrap-iterations", type=int, default=1000)

    arguments = parser.parse_args()
    if arguments.cmd == "prepare-source":
        lock = json.loads(Path(arguments.lock).read_text())
        result = prepare_source(
            [Path(value) for value in arguments.input],
            Path(arguments.output),
            lock["source_dataset"]["file_hashes"],
        )
    elif arguments.cmd == "recover-source":
        result = recover_source_profile(
            Path(arguments.prepared) / "features_blind_075.csv",
            Path(arguments.prepared) / "labels_sealed.csv",
            Path(arguments.lock),
            Path(arguments.recovery),
            Path(arguments.output),
        )
    elif arguments.cmd == "prepare-external":
        protocol = json.loads(Path(arguments.protocol).read_text())
        result = prepare_external(
            Path(arguments.input),
            Path(arguments.output),
            protocol["external_dataset"],
        )
    else:
        result = run_external(
            Path(arguments.prepared) / "features_blind_075.csv",
            Path(arguments.prepared) / "labels_sealed.csv",
            Path(arguments.profile),
            Path(arguments.protocol),
            Path(arguments.output),
            arguments.bootstrap_iterations,
        )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
