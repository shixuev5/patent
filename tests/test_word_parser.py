from __future__ import annotations

from pathlib import Path

import pytest

from agents.common.parsers import word_parser


def test_local_word_parser_converts_docx_with_pypandoc(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "out"

    calls: list[tuple[str, str, str, list[str]]] = []

    def fake_convert_file(source_path, to, format, outputfile, extra_args):
        calls.append((source_path, to, format, outputfile, extra_args))
        Path(outputfile).write_text("# converted\n", encoding="utf-8")

    monkeypatch.setattr(word_parser, "pypandoc", type("FakePypandoc", (), {"convert_file": staticmethod(fake_convert_file)})())

    result = word_parser.LocalWordParser().parse(source, output_dir)

    assert result == output_dir / "raw.md"
    assert result.read_text(encoding="utf-8") == "# converted\n"
    assert calls == [
        (
            str(source),
            "gfm",
            "docx",
            str(output_dir / "raw.md"),
            [f"--extract-media={output_dir / 'images'}"],
        )
    ]


def test_local_word_parser_converts_doc_via_libreoffice(monkeypatch, tmp_path) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"doc")
    output_dir = tmp_path / "out"

    def fake_run(cmd, check, capture_output, text):
        converted = output_dir / "libreoffice_tmp" / "legacy.docx"
        converted.parent.mkdir(parents=True, exist_ok=True)
        converted.write_bytes(b"docx")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    def fake_convert_file(source_path, to, format, outputfile, extra_args):
        assert source_path.endswith("legacy.docx")
        Path(outputfile).write_text("converted from doc\n", encoding="utf-8")

    monkeypatch.setattr(word_parser.LocalWordParser, "_resolve_soffice_binary", lambda self: "/usr/bin/soffice")
    monkeypatch.setattr(word_parser.subprocess, "run", fake_run)
    monkeypatch.setattr(word_parser, "pypandoc", type("FakePypandoc", (), {"convert_file": staticmethod(fake_convert_file)})())

    result = word_parser.LocalWordParser().parse(source, output_dir)

    assert result.read_text(encoding="utf-8") == "converted from doc\n"


def test_word_parser_falls_back_to_online_when_local_fails(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "out"
    fallback = output_dir / "raw.md"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text("online\n", encoding="utf-8")

    monkeypatch.setattr(word_parser.LocalWordParser, "parse", lambda self, file_path, target_dir: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(word_parser.OnlineWordParser, "parse", lambda self, file_path, target_dir: fallback)

    result = word_parser.WordParser.parse(source, output_dir)

    assert result == fallback


def test_word_parser_raises_when_local_and_online_fail(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.doc"
    source.write_bytes(b"doc")

    monkeypatch.setattr(word_parser.LocalWordParser, "parse", lambda self, file_path, target_dir: (_ for _ in ()).throw(RuntimeError("local failed")))
    monkeypatch.setattr(word_parser.OnlineWordParser, "parse", lambda self, file_path, target_dir: (_ for _ in ()).throw(RuntimeError("online failed")))

    with pytest.raises(RuntimeError, match="online failed"):
        word_parser.WordParser.parse(source, tmp_path / "out")
