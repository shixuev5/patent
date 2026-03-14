from __future__ import annotations

from types import SimpleNamespace

from backend.scripts.migrate_r2_task_keys_to_pn_dirs import migrate_task_artifacts_to_pn_layout
from backend.storage.r2_storage import R2Config, R2Storage
from backend.storage.sqlite_storage import SQLiteTaskStorage


def test_sqlite_patent_analysis_sha256_roundtrip(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "analysis_sha_test.db")

    assert storage.record_patent_analysis("cn1234567a", "ABCDEF")
    row_by_pn = storage.get_patent_analysis_by_pn("cn1234567a")
    assert row_by_pn is not None
    assert row_by_pn["pn"] == "CN1234567A"
    assert row_by_pn["sha256"] == "abcdef"

    # 空 sha 不应覆盖已有值
    assert storage.record_patent_analysis("CN1234567A", None)
    row_by_pn = storage.get_patent_analysis_by_pn("CN1234567A")
    assert row_by_pn is not None
    assert row_by_pn["sha256"] == "abcdef"

    # 新 sha 应覆盖旧值，并支持按 sha 查询
    assert storage.record_patent_analysis("CN1234567A", "0011")
    row_by_sha = storage.get_patent_analysis_by_sha256("0011")
    assert row_by_sha is not None
    assert row_by_sha["pn"] == "CN1234567A"
    assert row_by_sha["sha256"] == "0011"


def test_r2_key_layout_for_analysis_and_patent_json():
    storage = R2Storage(
        R2Config(
            endpoint_url="https://example.invalid",
            access_key_id="ak",
            secret_access_key="sk",
            bucket="bucket",
            enabled=False,
            key_prefix="workspace",
        )
    )
    assert storage.build_patent_pdf_key("cn123") == "workspace/CN123/ai_analysis.pdf"
    assert storage.build_analysis_json_key("cn123") == "workspace/CN123/ai_analysis.json"
    assert storage.build_patent_json_key("cn123") == "workspace/CN123/patent.json"
    assert storage.build_ai_review_pdf_key("cn123") == "workspace/CN123/ai_review.pdf"
    assert storage.build_ai_review_json_key("cn123") == "workspace/CN123/ai_review.json"
    assert storage.build_ai_reply_pdf_key("cn123") == "workspace/CN123/ai_reply.pdf"
    assert storage.build_ai_reply_json_key("cn123") == "workspace/CN123/ai_reply.json"
    assert storage.build_avatar_key("user-1", "a.png").startswith("avatar/")


class _FakeMigrationStorage:
    def __init__(self, keys: set[str], fail_copy_keys: set[str] | None = None):
        self.config = SimpleNamespace(key_prefix="workspace")
        self._keys = set(keys)
        self._fail_copy_keys = set(fail_copy_keys or set())
        self.copy_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def list_keys(self, prefix: str, max_keys: int = 1000) -> list[str]:
        return [key for key in sorted(self._keys) if key.startswith(prefix)][:max_keys]

    def key_exists(self, key: str) -> bool:
        return key in self._keys

    def copy_key(self, source_key: str, target_key: str) -> bool:
        self.copy_calls.append((source_key, target_key))
        if source_key in self._fail_copy_keys:
            return False
        if source_key not in self._keys:
            return False
        self._keys.add(target_key)
        return True

    def delete_key(self, key: str) -> bool:
        self.delete_calls.append(key)
        if key in self._keys:
            self._keys.remove(key)
            return True
        return False

    @staticmethod
    def _normalize_pn(value: str) -> str:
        return str(value or "").strip().upper() or "UNKNOWN"

    def build_patent_pdf_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/ai_analysis.pdf"

    def build_analysis_json_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/ai_analysis.json"

    def build_patent_json_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/patent.json"

    def build_ai_review_pdf_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/ai_review.pdf"

    def build_ai_review_json_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/ai_review.json"

    def build_ai_reply_pdf_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/ai_reply.pdf"

    def build_ai_reply_json_key(self, patent_number: str) -> str:
        pn = self._normalize_pn(patent_number)
        return f"workspace/{pn}/ai_reply.json"


def test_migrate_r2_task_artifacts_dry_run_reports_stats():
    storage = _FakeMigrationStorage(
        keys={
            "workspace/ai_analysis/CN1.pdf",
            "workspace/ai_analysis/CN1.json",
            "workspace/patent/CN1.json",
            "workspace/ai_review/CN1.pdf",
            "workspace/ai_review/CN1.json",
            "workspace/ai_reply/CN1.pdf",
            "workspace/ai_reply/CN1.json",
            "workspace/ai_analysis/README.txt",
            "workspace/CN1/ai_reply.pdf",
        }
    )

    stats = migrate_task_artifacts_to_pn_layout(
        storage,
        dry_run=True,
        limit=None,
        delete_source=False,
    )

    assert stats["total"] == 7
    assert stats["success"] == 7
    assert stats["failed"] == 0
    assert stats["skipped"] == 1
    assert stats["overwritten"] == 1
    assert stats["deleted"] == 0
    assert storage.copy_calls == []
    assert storage.delete_calls == []


def test_migrate_r2_task_artifacts_copy_and_delete():
    storage = _FakeMigrationStorage(
        keys={
            "workspace/ai_analysis/CN9.pdf",
            "workspace/ai_analysis/CN9.json",
            "workspace/CN9/ai_analysis.pdf",
        },
        fail_copy_keys={"workspace/ai_analysis/CN9.json"},
    )

    stats = migrate_task_artifacts_to_pn_layout(
        storage,
        dry_run=False,
        limit=None,
        delete_source=True,
    )

    assert stats["total"] == 2
    assert stats["success"] == 1
    assert stats["failed"] == 1
    assert stats["skipped"] == 0
    assert stats["overwritten"] == 1
    assert stats["deleted"] == 1
    assert ("workspace/ai_analysis/CN9.pdf", "workspace/CN9/ai_analysis.pdf") in storage.copy_calls
    assert "workspace/ai_analysis/CN9.pdf" in storage.delete_calls
    assert "workspace/ai_analysis/CN9.pdf" not in storage._keys
    assert "workspace/ai_analysis/CN9.json" in storage._keys


def test_migrate_r2_task_artifacts_respects_limit():
    storage = _FakeMigrationStorage(
        keys={
            "workspace/ai_reply/CN7.pdf",
            "workspace/ai_reply/CN7.json",
        },
    )

    stats = migrate_task_artifacts_to_pn_layout(
        storage,
        dry_run=False,
        limit=1,
        delete_source=False,
    )

    assert stats["total"] == 1
    assert stats["success"] == 1
    assert stats["failed"] == 0
    assert len(storage.copy_calls) == 1
