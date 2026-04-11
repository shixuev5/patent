from __future__ import annotations

from types import SimpleNamespace
import zipfile

from backend.ai_search.reporting import (
    build_ai_search_bundle,
    build_ai_search_report_markdown,
    classify_report_documents,
)


def test_classify_report_documents_supports_x_y_a() -> None:
    documents = [
        {"document_id": "doc-x", "pn": "CNX", "stage": "selected", "claim_ids_json": ["1"], "evidence_locations_json": ["paragraph_0001"]},
        {"document_id": "doc-y", "pn": "CNY", "stage": "selected", "claim_ids_json": ["2"], "evidence_locations_json": ["paragraph_0002"]},
        {"document_id": "doc-a", "pn": "CNA", "stage": "selected", "claim_ids_json": [], "evidence_locations_json": []},
    ]
    close_read_result = {
        "limitation_coverage": [
            {"claim_id": "1", "limitation_id": "L1", "status": "covered", "supporting_document_ids": ["doc-x"]},
            {"claim_id": "1", "limitation_id": "L2", "status": "covered", "supporting_document_ids": ["doc-x", "doc-y"]},
        ],
    }
    feature_compare_result = {
        "document_roles": [
            {"document_id": "doc-x", "role": "primary", "document_type_hint": "X"},
            {"document_id": "doc-y", "role": "combination", "document_type_hint": "Y"},
            {"document_id": "doc-a", "role": "background", "document_type_hint": "A"},
        ]
    }

    classified = classify_report_documents(documents, close_read_result, feature_compare_result)

    assert [item["document_type"] for item in classified] == ["X", "Y", "A"]
    assert [item["report_row_order"] for item in classified] == [1, 2, 3]


def test_build_ai_search_report_markdown_contains_required_document_columns() -> None:
    task = SimpleNamespace(pn="CN202600001A")
    markdown = build_ai_search_report_markdown(
        task=task,
        current_plan={"executionSpec": {"search_scope": {"objective": "测试目标"}}},
        documents=[
            {
                "document_id": "doc-1",
                "stage": "selected",
                "document_type": "X",
                "pn": "CN123456A",
                "publication_date": "20240102",
                "primary_ipc": "G06F 9/00",
                "evidence_summary": "说明书第01段；图2",
                "claim_ids_json": ["1", "2"],
            }
        ],
        feature_comparison=None,
        close_read_result={},
        feature_compare_result={},
        source_patent_data={
            "bibliographic_data": {
                "application_number": "CN202600001A",
                "filing_date": "2024.03.01",
                "priority_date": "2023.10.15",
                "applicants": ["申请人A"],
            },
            "claims": [{"id": "1"}],
            "description_paragraphs": [{"id": "p1"}],
        },
    )

    assert "相关专利文献" in markdown
    assert "类型" in markdown
    assert "国别以及代码[11]给出的文献号" in markdown
    assert "代码[43]或[45]给出的日期" in markdown
    assert "IPC分类号" in markdown
    assert "相关的段落和/或图号" in markdown
    assert "涉及的权利要求" in markdown
    assert "CN123456A" in markdown
    assert "说明书第01段；图2" in markdown
    assert "1-2" in markdown


def test_build_ai_search_report_markdown_renders_non_patent_documents() -> None:
    task = SimpleNamespace(pn="CN202600001A")
    markdown = build_ai_search_report_markdown(
        task=task,
        current_plan={"execution_spec": {"search_scope": {"objective": "测试目标"}}},
        documents=[
            {
                "document_id": "doc-npl-1",
                "stage": "selected",
                "document_type": "Y",
                "source_type": "openalex",
                "title": "Academic Paper",
                "doi": "10.1000/example",
                "venue": "Nature",
                "publication_date": "2023-09-01",
                "claim_ids_json": ["1"],
            }
        ],
        feature_comparison=None,
        close_read_result={},
        feature_compare_result={},
        source_patent_data={"bibliographic_data": {}},
    )

    assert "相关非专利文献" in markdown
    assert "Academic Paper" in markdown
    assert "10.1000/example" in markdown
    assert "OpenAlex / Nature" in markdown


def test_build_ai_search_bundle_excludes_json_and_markdown(tmp_path, monkeypatch) -> None:
    report_pdf = tmp_path / "ai_search_report.pdf"
    report_pdf.write_bytes(b"%PDF-1.4")
    feature_csv = tmp_path / "feature_comparison.csv"
    feature_csv.write_text("feature,doc\nA,CN1\n", encoding="utf-8")
    fake_doc_pdf = tmp_path / "CN1.pdf"
    fake_doc_pdf.write_bytes(b"%PDF-1.4 selected")

    monkeypatch.setattr(
        "backend.ai_search.reporting._download_selected_document_pdf",
        lambda pn, output_path: fake_doc_pdf if pn == "CN1" else None,
    )

    bundle_path = build_ai_search_bundle(
        output_path=tmp_path / "ai_search_result_bundle.zip",
        report_pdf_path=report_pdf,
        feature_comparison_csv_path=feature_csv,
        selected_documents=[{"pn": "CN1"}],
    )

    with zipfile.ZipFile(bundle_path, "r") as archive:
        names = sorted(archive.namelist())

    assert "ai_search_report.pdf" in names
    assert "feature_comparison.csv" in names
    assert "comparison_docs/CN1.pdf" in names
    assert all(not name.endswith(".json") for name in names)
    assert all(not name.endswith(".md") for name in names)
