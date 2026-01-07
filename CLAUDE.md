# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an AI-powered patent analysis pipeline that:
1. Parses PDF patent documents into structured Markdown using MinerU
2. Transforms unstructured text into structured JSON (bibliographic data, claims, descriptions, drawings)
3. Extracts technical component knowledge graphs (parts hierarchy, functions, relationships)
4. Performs OCR on patent figures and annotates them with component labels
5. Generates comprehensive patent analysis reports (technical problems, solutions, features, effects)
6. Renders final reports as Markdown and PDF

## Common Commands

### Environment Setup
```bash
# Install dependencies using uv
uv sync

# Activate virtual environment
source .venv/bin/activate
```

### Running the Pipeline
```bash
# Run the full pipeline (processes first PDF in input/ directory)
python main.py
```

### Environment Configuration
- Copy `.env.example` to `.env` and configure:
  - `LLM_API_KEY` and `LLM_BASE_URL`: Main LLM for structured extraction (supports OpenAI-compatible APIs)
  - `LLM_MODEL`: Model name (default: deepseek-chat)
  - `OCR_ENGINE`: Choose "local" (PaddleOCR) or "glm" (ZhipuAI GLM-4V)
  - `GLM_API_KEY` and `GLM_VISION_MODEL`: Required if using GLM for OCR
  - `MINERU_MODEL_SOURCE`: Model source for MinerU (default: modelscope)

### Input/Output Structure
- **Input**: Place PDF files in `input/` directory
- **Output**: Generated in `output/<pdf_filename>/`:
  - `mineru_raw/`: Raw Markdown and images from MinerU
  - `annotated_images/`: OCR-annotated patent figures
  - `patent.json`: Structured patent data
  - `parts.json`: Component knowledge graph
  - `image_parts.json`: Mapping of images to components
  - `report.json`: Analysis report data
  - `<filename>.md` and `<filename>.pdf`: Final reports

## Architecture

### Pipeline Stages (main.py)

The pipeline is stateful and resumable - each stage checks for cached outputs before running:

1. **PDFParser** (src/parser.py): Uses MinerU to convert PDF → Markdown + images
2. **PatentTransformer** (src/transformer.py): LLM-based structured extraction (Markdown → JSON)
3. **KnowledgeExtractor** (src/knowledge.py): Builds component relationship graph from patent text
4. **VisualProcessor** (src/vision.py): OCR + intelligent label placement on patent figures
5. **ContentGenerator** (src/generator.py): Multi-stage LLM analysis (macro logic → micro details → figure explanations)
6. **ReportRenderer** (src/renderer.py): Markdown assembly + Playwright-based PDF generation

### Key Design Patterns

**Structured Outputs with Pydantic**:
- All LLM interactions use `response_format={"type": "json_object"}` for reliability
- Pydantic models (e.g., `PatentDocument`, `BibliographicData`) enforce schema validation
- Always validate with `.model_validate()` after JSON parsing

**Context Management for Long Documents**:
- `KnowledgeExtractor._construct_context()`: Prioritized text selection (attachments → claims → details)
- `ContentGenerator`: Two-stage prompting (macro → micro) to stay within token limits
- Truncation strategies with semantic markers (e.g., "(下文已截断)")

**Visual Processing**:
- `VisualProcessor._process_single_image()`: OCR → knowledge base matching → annotation
- `LabelPlacer`: Intelligent label positioning algorithm that:
  - Builds obstacle maps from image edges
  - Finds optimal positions (right → left → below → above)
  - Prevents overlaps using occupied maps
- Supports both local (PaddleOCR) and cloud (GLM-4V) OCR engines

**Report Generation**:
- Multi-stage LLM prompts with role-based personas (patent examiner, technical expert)
- Structured output: technical problem → solution → means → features → effects
- Figure analysis includes component tables and AI-generated explanations

### Critical Files

- **config.py**: Centralized settings including LLM configs, paths, PDF CSS styling
- **main.py**: Pipeline orchestration with checkpoint/resume logic
- **src/llm.py**: LLM client abstraction (created based on git status)
- **assets/simhei.ttf**: Required Chinese font for PDF rendering (must be manually placed)

## Development Notes

### Working with LLM Prompts
- System prompts define role/persona (审查员助理, 技术专家)
- User prompts provide structured context with clear section markers (`【背景技术】`, `# 1. 待分析文本`)
- Always include output format requirements and JSON schema examples
- Temperature: 0.1 for extraction, 0.3 for creative text generation

### Adding New Pipeline Stages
1. Create new class in `src/`
2. Add instance in `main.py`
3. Implement checkpoint logic: check if output JSON exists before running
4. Update `config.py` if new paths needed via `get_project_paths()`

### Handling OCR Engines
- Local (PaddleOCR): Faster, no API costs, returns `rec_texts` + `rec_boxes`
- GLM (ZhipuAI): Better for complex figures, requires coordinate denormalization (0-1000 → pixels)
- Both return standardized format: `[{'text': str, 'box': [x1, y1, x2, y2]}, ...]`

### PDF Rendering
- Uses Playwright headless Chromium
- CSS defined in `config.py` with print-specific rules (`@page`, `page-break-after: avoid`)
- Images referenced as relative paths from temp HTML file
- Always clean up `temp_render.html` after generation
