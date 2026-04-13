from __future__ import annotations

from pathlib import Path

from agents.ai_reply.src.nodes.document_processing import DocumentProcessingNode


def test_parse_document_accepts_doc(monkeypatch, tmp_path) -> None:
    source = tmp_path / "office_action.doc"
    source.write_bytes(b"doc")
    output_dir = tmp_path / "parsed"
    target_md = output_dir / "raw.md"

    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.document_processing.WordParser.parse",
        lambda file_path, target_dir: target_md,
    )

    result = DocumentProcessingNode().parse_document(str(source), str(output_dir))

    assert result == str(target_md)


def test_parse_document_accepts_docx(monkeypatch, tmp_path) -> None:
    source = tmp_path / "office_action.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "parsed"
    target_md = output_dir / "raw.md"

    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.document_processing.WordParser.parse",
        lambda file_path, target_dir: target_md,
    )

    result = DocumentProcessingNode().parse_document(str(source), str(output_dir))

    assert result == str(target_md)


def test_document_processing_uses_zhihuiya_resolver_for_non_matching_comparison_doc(monkeypatch, tmp_path: Path) -> None:
    markdown_path = tmp_path / "office_action.md"
    markdown_path.write_text(
        """申请号：202610088597.2

# 第一次审查意见通知书

1、权利要求1相对于对比文件1(XX123456A1)不具备创造性。
""",
        encoding="utf-8",
    )

    class _FakeClient:
        def __init__(self):
            self.calls: list[str] = []

        def has_patent_record(self, document_number: str) -> bool:
            self.calls.append(document_number)
            return document_number == "XX123456A1"

    fake_client = _FakeClient()
    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.document_processing.SearchClientFactory.get_client",
        lambda provider="zhihuiya": fake_client,
    )

    node = DocumentProcessingNode()
    office_action = node.extract_office_action_structured_data(str(markdown_path))

    assert office_action["comparison_documents"][0]["document_number"] == "XX123456A1"
    assert office_action["comparison_documents"][0]["is_patent"] is True
    assert fake_client.calls == ["XX123456A1"]
