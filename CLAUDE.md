```
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
```

## Patent Analysis System - Codebase Overview

This is a **Python-based Patent Analysis System** that processes patent documents through a structured pipeline to extract information, analyze content, and generate comprehensive reports. The system combines natural language processing, computer vision, and retrieval-augmented generation (RAG) to automate patent analysis workflows.

## Key Architecture

### System Pipeline
The `PatentPipeline` class in `main.py` orchestrates the end-to-end workflow:
1. **Download Patent**: Using Zhihuiya API
2. **Parse PDF**: Convert PDF to Markdown using Mineru (local or API)
3. **Transform to Structured Data**: Use LLM to parse Markdown into structured patent JSON
4. **Extract Knowledge**: Identify and extract technical entities/parts from content
5. **Visual Processing**: OCR on patent images and match parts to text descriptions
6. **Formal Defect Check**: Verify consistency between text descriptions and figure labels
7. **Content Generation**: Generate report sections using LLM
8. **Search & Retrieval**: LangGraph-based search for prior art and examination
9. **Render Report**: Convert Markdown to HTML to PDF using Playwright

### Directory Structure
```
d:\Codes\patent\
├── main.py                 # Entry point and pipeline orchestrator
├── config.py               # Configuration management (paths, API keys, settings)
├── pyproject.toml          # Project dependencies and metadata
├── uv.lock                 # Dependency lock file (uv package manager)
├── .env                    # Environment variables (API keys, configurations)
├── .env.example            # Example environment file with placeholders
├── assets/                 # Static assets (fonts: simhei.ttf)
├── output/                 # Generated outputs (patent-specific directories)
└── src/                    # Main source code
    ├── parser.py           # PDF parsing (Mineru API or local)
    ├── transformer.py      # Markdown to structured patent data (LLM-based)
    ├── knowledge.py        # Entity/parts extraction from patent content
    ├── vision.py           # Image processing and OCR (PaddleOCR)
    ├── checker.py          # Formal defect checks (part-text consistency)
    ├── generator.py        # Report content generation (LLM-based)
    ├── renderer.py         # Report rendering (HTML -> PDF via Playwright)
    ├── search_clients/     # Patent search APIs (Zhihuiya/PatSnap)
    ├── graph/              # LangGraph-based search workflow
    └── utils/              # Utility modules (LLM, cache, crypto, reranker)
```

## Development Commands

### Dependency Management
The project uses **uv** (Python package manager):
```bash
uv sync                    # Install dependencies
uv pip install <package>   # Install a specific package
uv pip freeze              # List installed packages
```

### Running the Pipeline
```bash
# Process a single patent
uv run python main.py --pn CN116745575A

# Process multiple patents (comma-separated)
uv run python main.py --pn CN116745575A,CN123456789

# Process patents from a file (one per line)
uv run python main.py --file patents.txt

# Run with multiple workers (parallel processing)
uv run python main.py --pn CN116745575A --workers 3
```

### Configuration
- **Environment Variables**: Copy `.env.example` to `.env` and fill in API keys
- **Settings**: Modify `config.py` for path configurations, LLM model settings, and API endpoints

## Technology Stack

### Core Dependencies
- **LangGraph**: Stateful workflow orchestration
- **OpenAI/DeepSeek API**: LLM processing for text generation and reasoning
- **Mineru**: PDF parsing and conversion to Markdown
- **PaddleOCR**: Optical character recognition on patent images
- **Playwright**: Browser automation for HTML to PDF rendering
- **Zhihuiya API**: Patent document search and download
- **Pydantic**: Data validation and structured data modeling

### Output Structure
Each processed patent creates a directory in `output/` with:
- `raw.pdf` - Original patent document
- `patent.json` - Structured patent data
- `parts.json` - Extracted technical parts/entities
- `image_parts.json` - Image-to-part mappings
- `check.json` - Formal defect check results
- `report.json` - Generated report content
- `{pn}.md` and `{pn}.pdf` - Final report in Markdown and PDF formats

## Key Modules

### 1. Search Clients (`src/search_clients/`)
- Factory pattern for different patent search APIs
- Zhihuiya API implementation for downloading patents
- Extensible to support other search providers (PatSnap, etc.)

### 2. LangGraph Search Workflow (`src/graph/`)
- Stateful search orchestration with LangGraph
- Nodes: brain (query generation), eye (search execution), hand (result processing), reporter (reporting)
- Query relaxation and document reranking for improved search results

### 3. Visual Processing (`src/vision.py`)
- PaddleOCR for text extraction from patent figures
- Image annotation with part labels
- Matching OCR text to extracted technical parts

### 4. Report Generation (`src/generator.py`, `src/renderer.py`)
- LLM-based content generation for report sections
- Markdown to HTML to PDF rendering with Playwright
- Custom CSS styling for professional report formatting
