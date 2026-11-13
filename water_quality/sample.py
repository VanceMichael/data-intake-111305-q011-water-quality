from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Observation:
    source: str
    key: str
    revision: int
    value: Any

def inspect_record(raw: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化一条来源观测，同时保留来源信息。"""
    required = ("source", "key", "revision", "value")
    missing = [name for name in required if name not in raw]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
    source, key = str(raw["source"]).strip(), str(raw["key"]).strip()
    if not source or not key:
        raise ValueError("source and key must be non-empty")
    revision = raw["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("revision must be a non-negative integer")
    item = Observation(source, key, revision, raw["value"])
    return {"source": item.source, "key": item.key, "revision": item.revision, "value": item.value, "event": "measurement_semantics"}
