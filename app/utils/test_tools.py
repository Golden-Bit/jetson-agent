# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any, Set
from pydantic import BaseModel, Field, root_validator, validator, conint, confloat
from langchain_core.tools import StructuredTool
import hashlib
from datetime import datetime

# opzionale: usa il tuo wrapper già presente
def _wrap_args(model_cls, impl_fn):
    def _inner(**kwargs):
        payload = kwargs.get("args", kwargs)
        args_obj = model_cls(**(payload or {}))
        return impl_fn(args_obj)
    return _inner

# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA “RICCO”
# ─────────────────────────────────────────────────────────────────────────────

class PingMode:
    PING:  Literal["ping"]     = "ping"
    ECHO:  Literal["echo"]     = "echo"
    CHK:   Literal["checksum"] = "checksum"
    STATS: Literal["stats"]    = "stats"

class Window(BaseModel):
    by: Literal["index","date"] = Field(..., description="Selezione per indici o per date")
    idx_start: Optional[conint(ge=0)] = None
    idx_end:   Optional[conint(ge=0)] = None
    date_start: Optional[str] = None
    date_end:   Optional[str] = None

class Meta(BaseModel):
    source: Literal["ui","api","cron"] = "ui"
    priority: conint(ge=0, le=10) = 5
    ts_iso: Optional[str] = Field(None, description="Timestamp ISO; se assente viene impostato lato tool")


class Item(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    value: Optional[confloat(ge=-1e9, le=1e9)] = None
    tags: List[str] = Field(default_factory=list, description="max 8 tag")

class PingComplexArgs(BaseModel):
    mode: Literal["ping","echo","checksum","stats"] = "ping"
    message: Optional[str] = Field("", description="Testo sorgente per echo/checksum")
    flags: Set[Literal["uppercase","reverse","dedup_tags","sort_items"]] = Field(default_factory=set)
    meta: Meta = Field(default_factory=Meta)
    items: List[Item] = Field(default_factory=list)
    window: Optional[Window] = None  # opzionale, solo per “gonfiare” lo schema

# ─────────────────────────────────────────────────────────────────────────────
# IMPLEMENTAZIONE
# ─────────────────────────────────────────────────────────────────────────────

def ping_complex_impl(args: PingComplexArgs) -> Dict[str, Any]:
    """
    Tool di test con schema complesso. Ritorna sempre un output strutturato.
    - ping      → "PONG"
    - echo      → trasforma message in base ai flags
    - checksum  → sha256(message + ids+values)
    - stats     → aggrega items (count, media, tag unici)
    """
    out: Dict[str, Any] = {"mode": args.mode, "meta": args.meta.dict()}
    msg = (args.message or "")

    def _transform_message(s: str) -> str:
        t = s
        if "uppercase" in args.flags:
            t = t.upper()
        if "reverse" in args.flags:
            t = t[::-1]
        return t

    if args.mode == "ping":
        out["result"] = "PONG"
        out["info"] = {"flags": list(args.flags), "items_count": len(args.items)}
        return out

    if args.mode == "echo":
        out["result"] = _transform_message(msg)
        out["info"] = {"len": len(out["result"])}
        return out

    if args.mode == "checksum":
        h = hashlib.sha256()
        h.update(msg.encode("utf-8"))
        for it in args.items:
            h.update(it.id.encode("utf-8"))
            if it.value is not None:
                h.update(str(it.value).encode("utf-8"))
        out["result"] = h.hexdigest()
        out["info"] = {"items": len(args.items)}
        return out

    # stats
    vals = [float(it.value) for it in args.items if it.value is not None]
    all_tags = []
    for it in args.items:
        all_tags.extend(it.tags)
    if "dedup_tags" in args.flags:
        all_tags = sorted(set(all_tags))
    elif "sort_items" in args.flags:  # giusto per differenziare i rami
        all_tags = sorted(all_tags)

    count = len(vals)
    mean = (sum(vals) / count) if count else None
    out["result"] = {"count": len(args.items), "with_values": count, "mean_value": mean, "tags": all_tags}
    return out

# ─────────────────────────────────────────────────────────────────────────────
# ESPOSIZIONE COME StructuredTool
# ─────────────────────────────────────────────────────────────────────────────

ping_complex = StructuredTool.from_function(
    func=_wrap_args(PingComplexArgs, ping_complex_impl),
    name="ping_complex",
    description="Ping di test con schema complesso (mode: ping|echo|checksum|stats). Richiede 'message' per echo/checksum.",
    args_schema=PingComplexArgs,
)
