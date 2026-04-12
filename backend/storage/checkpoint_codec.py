"""Checkpoint typed-value codec helpers."""

from __future__ import annotations

import base64
import json
from typing import Any, Tuple


def encode_typed_value(value: Tuple[str, bytes]) -> str:
    kind, payload = value
    return json.dumps(
        {
            "type": kind,
            "data": base64.b64encode(payload).decode("ascii"),
        },
        ensure_ascii=False,
    )


def decode_typed_value(raw: Any) -> Tuple[str, bytes]:
    if isinstance(raw, tuple) and len(raw) == 2:
        kind, payload = raw
        if isinstance(kind, str) and isinstance(payload, (bytes, bytearray)):
            return kind, bytes(payload)
    if not isinstance(raw, str):
        raise ValueError("typed value payload must be a JSON string")
    data = json.loads(raw)
    kind = str(data.get("type") or "")
    payload_b64 = str(data.get("data") or "")
    return kind, base64.b64decode(payload_b64.encode("ascii"))
