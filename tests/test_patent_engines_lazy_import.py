import importlib
import sys


def test_package_init_does_not_import_vision_for_knowledge_only():
    sys.modules.pop("agents.common.patent_engines", None)
    sys.modules.pop("agents.common.patent_engines.vision", None)

    package = importlib.import_module("agents.common.patent_engines")

    assert "agents.common.patent_engines.vision" not in sys.modules

    knowledge_extractor = package.KnowledgeExtractor

    assert knowledge_extractor.__name__ == "KnowledgeExtractor"
    assert "agents.common.patent_engines.vision" not in sys.modules


def test_langchain_compat_exposes_legacy_modules():
    compat = importlib.import_module("agents.common.utils.langchain_compat")
    compat.install_langchain_compat()

    from langchain.docstore.document import Document
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document as CoreDocument

    splitter = RecursiveCharacterTextSplitter(chunk_size=4, chunk_overlap=1)

    assert Document is CoreDocument
    assert splitter.split_text("abcdef") == ["abcd", "def"]
