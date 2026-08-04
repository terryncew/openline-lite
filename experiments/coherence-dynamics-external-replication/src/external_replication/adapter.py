from __future__ import annotations
import hashlib, json, math, re
from pathlib import Path
from typing import Any, Iterable, Mapping
from public_trajectory_audit.nebius import BlindRecord

ALLOWED_SOURCES={"swe-smith-claude-3-7-sonnet","kwai-klear-swe-smith-mini"}
EXCLUDED_SOURCES={"nebius-swe-rebench-openhands"}
EXTERNAL_COLUMNS=("session_id","source_dataset","source_id","recorded_model","messages_json","ground_truth_meta_json")

class ExternalSchemaError(ValueError): pass

def _text(value: Any) -> str:
    if value is None: return ""
    if isinstance(value,str): return value
    if isinstance(value,list):
        parts=[]
        for item in value:
            if isinstance(item,str): parts.append(item)
            elif isinstance(item,Mapping) and isinstance(item.get("text"),str): parts.append(item["text"])
            else: parts.append(json.dumps(item,sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str))
        return "\n".join(parts)
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,default=str)

def _tool_calls(value: Any) -> str:
    if value in (None,"",[]): return ""
    if isinstance(value,str):
        try: value=json.loads(value)
        except json.JSONDecodeError: return value
    calls=value if isinstance(value,list) else [value]
    lines=[]
    for call in calls:
        if not isinstance(call,Mapping):
            lines.append(_text(call)); continue
        fn=call.get("function") if isinstance(call.get("function"),Mapping) else call
        name=fn.get("name") or call.get("name") or "tool"
        args=fn.get("arguments") or call.get("arguments") or ""
        lines.append(f"{name} {_text(args)}".strip())
    return "\n".join(lines)

def normalize_messages(messages_json: Any) -> tuple[dict[str,str],...]:
    if isinstance(messages_json,str): messages=json.loads(messages_json)
    else: messages=messages_json
    if not isinstance(messages,list): raise ExternalSchemaError("messages_json must decode to list")
    out=[]
    for i,msg in enumerate(messages):
        if not isinstance(msg,Mapping): raise ExternalSchemaError(f"message {i} must be object")
        role=msg.get("role")
        if role=="assistant": mapped="ai"
        elif role in {"tool","function"}: mapped="user"
        elif role in {"system","user","ai"}: mapped=role
        else: raise ExternalSchemaError(f"unsupported role: {role!r}")
        text=_text(msg.get("content"))
        calls=_tool_calls(msg.get("tool_calls_json",msg.get("tool_calls"))) if mapped=="ai" else ""
        if calls: text=(text+"\n"+calls).strip()
        out.append({"role":mapped,"text":text})
    return tuple(out)

def repository_from_external(instance_id: str) -> str:
    first=instance_id.split(".",1)[0]
    if "__" in first: return first
    m=re.match(r"^(?P<repo>.+)-\d+$",instance_id)
    return m.group("repo") if m else first

def parse_meta(value: Any) -> dict[str,Any]:
    if isinstance(value,str): value=json.loads(value)
    if not isinstance(value,dict): raise ExternalSchemaError("ground_truth_meta_json must decode to object")
    return value

def record_and_label(row: Mapping[str,Any]) -> tuple[BlindRecord,dict[str,Any]]:
    unknown=set(row)-set(EXTERNAL_COLUMNS)
    if unknown: raise ExternalSchemaError(f"unexpected source columns: {sorted(unknown)}")
    source=row.get("source_dataset")
    if source in EXCLUDED_SOURCES: raise ExternalSchemaError("excluded Nebius-derived row reached extractor")
    if source not in ALLOWED_SOURCES: raise ExternalSchemaError(f"unfrozen source cohort: {source!r}")
    session_id=row.get("session_id"); source_id=row.get("source_id"); model=row.get("recorded_model")
    if not all(isinstance(v,str) and v for v in (session_id,source_id,model)): raise ExternalSchemaError("identity fields must be non-empty strings")
    meta=parse_meta(row.get("ground_truth_meta_json"))
    resolved=meta.get("resolved")
    if not isinstance(resolved,(bool,int)): raise ExternalSchemaError("resolved label must be bool")
    instance_id=meta.get("instance_id") if isinstance(meta.get("instance_id"),str) and meta.get("instance_id") else source_id
    trajectory=normalize_messages(row.get("messages_json"))
    identity=hashlib.sha256(json.dumps({"session_id":session_id,"messages":trajectory},sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")).hexdigest()
    rec=BlindRecord(identity,instance_id,model,repository_from_external(instance_id),trajectory)
    label={"trajectory_id":identity,"target":int(bool(resolved)),"source_dataset":source,"source_id":source_id,"session_id":session_id,"instance_id":instance_id,"repository":rec.repository,"model_name":model,"task_group":instance_id}
    return rec,label

def iter_external_rows(path: Path,batch_size:int=256)->Iterable[dict[str,Any]]:
    if path.suffix==".jsonl":
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                row=json.loads(line)
                yield {k:row[k] for k in EXTERNAL_COLUMNS}
        return
    if path.suffix!=".parquet": raise ExternalSchemaError("external input must be parquet or jsonl")
    import pyarrow.parquet as pq
    pf=pq.ParquetFile(path)
    missing=[c for c in EXTERNAL_COLUMNS if c not in pf.schema_arrow.names]
    if missing: raise ExternalSchemaError(f"missing external columns: {missing}")
    for batch in pf.iter_batches(columns=list(EXTERNAL_COLUMNS),batch_size=batch_size,use_threads=True):
        yield from batch.to_pylist()
