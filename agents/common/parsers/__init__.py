"""
Document parsers for patent analysis.
This module provides interfaces and implementations for parsing various document formats.
"""

from agents.common.parsers.base import BaseParser
from agents.common.parsers.pdf_parser import LocalPDFParser, OnlinePDFParser, PDFParser
from agents.common.parsers.word_parser import LocalWordParser, WordParser

__all__ = ["BaseParser", "LocalPDFParser", "OnlinePDFParser", "PDFParser", "LocalWordParser", "WordParser"]