from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

SOURCE_COLUMNS = ("instance_id", "model_name", "target", "trajectory")


def iter_rows(path: Path, *, columns: Sequence[str] = SOURCE_COLUMNS, batch_size: int = 256) -> Iterable[dict[str, Any]]:
    """Stream only the declared source columns.

    The large patch and evaluation-log fields are never loaded by the audit
    process. This is both a memory boundary and a leakage boundary.
    """
    wanted = tuple(columns)
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: row must be an object")
                missing = [name for name in wanted if name not in row]
                if missing:
                    raise ValueError(f"{path}:{line_number}: missing columns: {missing}")
                yield {name: row[name] for name in wanted}
        return
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError("Parquet input requires pyarrow") from exc
        parquet = pq.ParquetFile(path)
        available = set(parquet.schema_arrow.names)
        missing = [name for name in wanted if name not in available]
        if missing:
            raise ValueError(f"{path}: missing columns: {missing}")
        for batch in parquet.iter_batches(columns=list(wanted), batch_size=batch_size, use_threads=True):
            for row in batch.to_pylist():
                yield row
        return
    raise ValueError(f"unsupported input type: {path.suffix}")
