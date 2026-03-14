from __future__ import annotations

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
    assert storage.build_patent_pdf_key("cn123") == "workspace/ai_analysis/CN123.pdf"
    assert storage.build_analysis_json_key("cn123") == "workspace/ai_analysis/CN123.json"
    assert storage.build_patent_json_key("cn123") == "workspace/patent/CN123.json"
