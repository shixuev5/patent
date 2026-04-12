"""
Persistent LangGraph checkpointer backed by task storage.
"""

from __future__ import annotations

import random
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langchain_core.runnables import RunnableConfig

from backend.storage.checkpoint_codec import decode_typed_value, encode_typed_value


class AiSearchCheckpointSaver(BaseCheckpointSaver[str]):
    def __init__(self, storage: Any):
        super().__init__()
        self.storage = storage

    def _load_blobs(
        self,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> Dict[str, Any]:
        rows = self.storage.get_ai_search_checkpoint_blobs(thread_id, checkpoint_ns, versions)
        channel_values: Dict[str, Any] = {}
        for channel, typed_json in rows.items():
            kind, payload = decode_typed_value(typed_json)
            if kind != "empty":
                channel_values[channel] = self.serde.loads_typed((kind, payload))
        return channel_values

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        row = self.storage.get_ai_search_checkpoint(thread_id, checkpoint_ns, checkpoint_id)
        if not row:
            return None
        checkpoint = self.serde.loads_typed(decode_typed_value(row["checkpoint_json"]))
        metadata = self.serde.loads_typed(decode_typed_value(row["metadata_json"]))
        writes = self.storage.list_ai_search_checkpoint_writes(
            thread_id,
            checkpoint_ns,
            row["checkpoint_id"],
        )
        pending_writes: list[PendingWrite] = []
        for item in writes:
            kind, payload = decode_typed_value(item["typed_value_json"])
            pending_writes.append((item["task_id"], item["channel"], self.serde.loads_typed((kind, payload))))
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": row["checkpoint_id"],
                }
            },
            checkpoint={
                **checkpoint,
                "channel_values": self._load_blobs(
                    thread_id,
                    checkpoint_ns,
                    checkpoint["channel_versions"],
                ),
            },
            metadata=metadata,
            pending_writes=pending_writes,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": row["parent_checkpoint_id"],
                    }
                }
                if row.get("parent_checkpoint_id")
                else None
            ),
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if not config:
            return iter(())
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        before_checkpoint_id = get_checkpoint_id(before) if before else None
        rows = self.storage.list_ai_search_checkpoints(
            thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            before_checkpoint_id=before_checkpoint_id,
            limit=limit,
        )
        for row in rows:
            tuple_value = self.get_tuple(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": row["checkpoint_ns"],
                        "checkpoint_id": row["checkpoint_id"],
                    }
                }
            )
            if not tuple_value:
                continue
            if filter and not all(tuple_value.metadata.get(key) == value for key, value in filter.items()):
                continue
            yield tuple_value

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        checkpoint_copy = checkpoint.copy()
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        values = checkpoint_copy.pop("channel_values")

        blob_records = []
        for channel, version in new_versions.items():
            typed_value = self.serde.dumps_typed(values[channel]) if channel in values else ("empty", b"")
            blob_records.append(
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "channel": channel,
                    "version": str(version),
                    "typed_value_json": encode_typed_value(typed_value),
                }
            )
        self.storage.put_ai_search_checkpoint_blobs(blob_records)
        self.storage.put_ai_search_checkpoint(
            {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
                "checkpoint_json": encode_typed_value(self.serde.dumps_typed(checkpoint_copy)),
                "metadata_json": encode_typed_value(self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))),
                "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
            }
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        checkpoint_id = str(config["configurable"]["checkpoint_id"])
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = str(config["configurable"].get("checkpoint_ns", ""))
        existing = {
            (item["task_id"], int(item["write_idx"]))
            for item in self.storage.list_ai_search_checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id)
        }
        records = []
        for idx, (channel, value) in enumerate(writes):
            write_idx = WRITES_IDX_MAP.get(channel, idx)
            key = (task_id, write_idx)
            if write_idx >= 0 and key in existing:
                continue
            records.append(
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "write_idx": write_idx,
                    "channel": channel,
                    "typed_value_json": encode_typed_value(self.serde.dumps_typed(value)),
                    "task_path": task_path,
                }
            )
        self.storage.put_ai_search_checkpoint_writes(records)

    def delete_thread(self, thread_id: str) -> None:
        self.storage.delete_ai_search_thread_checkpoints(thread_id)

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"
