from __future__ import annotations

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
