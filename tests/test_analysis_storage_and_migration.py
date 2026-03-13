from __future__ import annotations

from types import SimpleNamespace

from backend.scripts import migrate_r2_analysis_pdfs as migration
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
    assert storage.build_patent_pdf_key("cn123") == "workspace/analysis/CN123.pdf"
    assert storage.build_analysis_json_key("cn123") == "workspace/analysis/CN123.json"
    assert storage.build_patent_json_key("cn123") == "workspace/patent/CN123.json"


def test_migrate_old_report_pdfs_idempotent(monkeypatch):
    class FakeStorage:
        def __init__(self):
            self.enabled = True
            self.config = SimpleNamespace(key_prefix="workspace")
            self.source_keys = [
                "workspace/reports/CN100.pdf",
                "workspace/reports/CN200.pdf",
                "workspace/reports/readme.txt",
            ]
            self.existing_targets = {"workspace/analysis/CN200.pdf"}
            self.copied = []
            self.deleted = []

        def list_keys(self, prefix, max_keys=1000):
            assert prefix == "workspace/reports/"
            return list(self.source_keys)

        def build_patent_pdf_key(self, pn):
            return f"workspace/analysis/{str(pn).upper()}.pdf"

        def key_exists(self, key):
            return key in self.existing_targets

        def copy_key(self, source_key, target_key):
            self.copied.append((source_key, target_key))
            self.existing_targets.add(target_key)
            return True

        def delete_key(self, key):
            self.deleted.append(key)
            return True

    fake_storage = FakeStorage()
    monkeypatch.setattr(migration, "_build_r2_storage", lambda: fake_storage)

    stats = migration.migrate_old_report_pdfs(delete_source=False)
    assert stats.total == 3
    assert stats.success == 1
    assert stats.skipped_exists == 1
    assert stats.skipped_invalid == 1
    assert stats.failed == 0
    assert stats.deleted == 0
    assert fake_storage.copied == [
        ("workspace/reports/CN100.pdf", "workspace/analysis/CN100.pdf")
    ]

    # 再次运行应幂等：目标已存在后跳过
    stats_second = migration.migrate_old_report_pdfs(delete_source=False)
    assert stats_second.success == 0
    assert stats_second.skipped_exists == 2
