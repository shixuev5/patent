"""粗筛子代理的响应结构。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class CoarseScreenOutput(BaseModel):
    keep: List[str] = Field(default_factory=list)
    discard: List[str] = Field(default_factory=list)
