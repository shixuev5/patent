from __future__ import annotations

import json

from agents.ai_reply.src.nodes.patent_retrieval import PatentRetrievalNode


class _FakeSearchClient:
    def __init__(self, publication_number: str = "CN115655695A"):
        self.publication_number = publication_number
        self.application_number_calls: list[str] = []

    def get_publication_number_by_application_number(self, application_number: str):
        self.application_number_calls.append(application_number)
        return self.publication_number


class _FakeR2Storage:
    def __init__(self, payload_by_key: dict[str, bytes] | None = None):
        self.enabled = True
        self.payload_by_key = payload_by_key or {}
        self.get_calls: list[str] = []
        self.put_calls: list[dict[str, object]] = []

    def build_patent_json_key(self, patent_number: str) -> str:
        return f"workspace/{str(patent_number or '').strip().upper()}/patent.json"

    def get_bytes(self, key: str):
        self.get_calls.append(key)
        return self.payload_by_key.get(key)

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream"):
        self.put_calls.append(
            {
                "key": key,
                "content": content,
                "content_type": content_type,
            }
        )
        self.payload_by_key[key] = content
        return True


def test_retrieve_single_patent_reuses_r2_patent_json_for_original_patent(monkeypatch, tmp_path) -> None:
    patent_data = {
        "publication_number": "CN115655695A",
        "title": "一种设备",
        "claims": [{"claim_id": "1", "claim_text": "一种设备。"}],
    }
    payload = json.dumps(patent_data, ensure_ascii=False).encode("utf-8")
    fake_r2 = _FakeR2Storage(
        payload_by_key={
            "workspace/CN115655695A/patent.json": payload,
        }
    )
    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.patent_retrieval._build_r2_storage",
        lambda: fake_r2,
    )

    node = PatentRetrievalNode()
    node.search_client = _FakeSearchClient()

    monkeypatch.setattr(
        node,
        "download_patent",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not download patent")),
    )
    monkeypatch.setattr(
        node,
        "parse_patent_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not parse patent")),
    )
    monkeypatch.setattr(
        node,
        "extract_patent_structured_data",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not extract patent")),
    )

    result = node._retrieve_single_patent(
        "202310001234.5",
        str(tmp_path),
        prefer_cached_patent_json=True,
    )

    assert result == {"202310001234.5": patent_data}
    assert fake_r2.get_calls == [
        "workspace/202310001234.5/patent.json",
        "workspace/CN115655695A/patent.json",
    ]
    assert node.search_client.application_number_calls == ["202310001234.5"]

    patent_json_path = tmp_path / "patent_202310001234.5" / "patent.json"
    assert patent_json_path.exists()
    assert json.loads(patent_json_path.read_text(encoding="utf-8")) == patent_data


def test_retrieve_single_patent_falls_back_to_download_parse_when_r2_patent_json_missing(
    monkeypatch,
    tmp_path,
) -> None:
    fake_r2 = _FakeR2Storage()
    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.patent_retrieval._build_r2_storage",
        lambda: fake_r2,
    )

    node = PatentRetrievalNode()
    node.search_client = _FakeSearchClient()

    download_calls: list[str] = []
    parse_calls: list[str] = []
    extract_calls: list[str] = []

    def _fake_download_patent(document_number: str, output_dir: str) -> str:
        download_calls.append(document_number)
        pdf_path = tmp_path / "downloaded.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")
        return str(pdf_path)

    def _fake_parse_patent_document(patent_path: str, output_dir: str) -> str:
        parse_calls.append(patent_path)
        markdown_path = tmp_path / "downloaded.md"
        markdown_path.write_text("# patent", encoding="utf-8")
        return str(markdown_path)

    def _fake_extract_patent_structured_data(markdown_path: str):
        extract_calls.append(markdown_path)
        return {"title": "fallback"}

    monkeypatch.setattr(node, "download_patent", _fake_download_patent)
    monkeypatch.setattr(node, "parse_patent_document", _fake_parse_patent_document)
    monkeypatch.setattr(node, "extract_patent_structured_data", _fake_extract_patent_structured_data)

    result = node._retrieve_single_patent(
        "202310001234.5",
        str(tmp_path),
        prefer_cached_patent_json=True,
    )

    assert result == {"202310001234.5": {"title": "fallback"}}
    assert fake_r2.get_calls == [
        "workspace/202310001234.5/patent.json",
        "workspace/CN115655695A/patent.json",
    ]
    assert fake_r2.put_calls[0]["key"] == "workspace/CN115655695A/patent.json"
    assert fake_r2.put_calls[0]["content_type"] == "application/json"
    assert json.loads(fake_r2.put_calls[0]["content"].decode("utf-8")) == {"title": "fallback"}
    assert download_calls == ["202310001234.5"]
    assert len(parse_calls) == 1
    assert len(extract_calls) == 1
    patent_json_path = tmp_path / "patent_202310001234.5" / "patent.json"
    assert patent_json_path.exists()
    assert json.loads(patent_json_path.read_text(encoding="utf-8")) == {"title": "fallback"}
