# CLAUDE.md

This document provides implementation guidance for working in this repository.

## Project Snapshot

Patent analysis platform with two agent workflows:
- `patent_analysis`: single/multi patent analysis pipeline (download/parse/structure/extract/vision/check/generate/render)
- `office_action_reply`: LangGraph workflow for office-action rebuttal assistance

Runtime stack:
- Backend: FastAPI (`backend/main.py`)
- Frontend: Nuxt 3 (`frontend/`)
- Agents: Python modules under `agents/`

## Current Directory Layout

```text
/Users/yanhao/Documents/codes/patent
├── backend/                     # FastAPI API + task orchestration + storage adapters
├── frontend/                    # Nuxt3 UI
├── agents/
│   ├── patent_analysis/         # Patent analysis pipeline
│   │   ├── main.py
│   │   └── src/
│   │       ├── knowledge.py
│   │       ├── vision.py
│   │       ├── checker.py
│   │       ├── generator.py
│   │       ├── search.py
│   │       └── renderer.py
│   ├── office_action_reply/     # Office action reply LangGraph workflow
│   │   ├── main.py
│   │   └── src/
│   │       ├── state.py
│   │       ├── edges.py
│   │       └── nodes/
│   └── common/                  # Shared modules: parsers/search_clients/retrieval/rendering/utils
├── config.py                    # Global settings and path conventions
├── .env.example                 # Environment variable template
├── pyproject.toml               # Python deps (uv)
└── tests/                       # Current test coverage (retrieval service)
```

## Setup & Run

### Python Dependencies

```bash
uv sync
```

### Run Backend API

```bash
uv run uvicorn backend.main:app --host 0.0.0.0 --port 7860 --reload
```

### Run Patent Analysis Agent (CLI)

```bash
# Single patent
uv run python -m agents.patent_analysis.main --pn CN116745575A

# Multiple patents (comma-separated)
uv run python -m agents.patent_analysis.main --pn CN116745575A,CN123456789A

# Batch from file (one PN per line)
uv run python -m agents.patent_analysis.main --file patents.txt
```

### Run Office Action Reply Agent (CLI)

```bash
uv run python -m agents.office_action_reply.main \
  --office-action "审查意见通知书.pdf" \
  --response "意见陈述书.docx" \
  --claims "权利要求书.pdf" \
  --comparison-docs "对比文件1.pdf,对比文件2.pdf"
```

Notes:
- `office_action_reply` CLI currently auto-generates `output/<task_id>/` and does not expose a `--output` argument.
- Backend task execution entry is in `backend/routes/tasks.py`.

## API Endpoints (Current)

- `POST /api/auth/guest`
- `GET /api/health`
- `GET /api/usage`
- `GET /api/changelog`
- `GET /api/tasks`
- `POST /api/tasks`
- `GET /api/tasks/{task_id}`
- `DELETE /api/tasks/{task_id}`
- `DELETE /api/tasks`
- `GET /api/tasks/{task_id}/progress` (SSE)
- `GET /api/tasks/{task_id}/download`

## Logging Convention

Logging is centralized in:
- `backend/logging_setup.py`
- `backend/log_context.py`

Current output format includes:
- UTC+8 timestamp
- level
- `task_id`
- task type label
- `pn`
- stage
- message

Agent log message normalization is enforced for `agents.*` modules:
- Unified message style: `[agent.component] message`
- Existing ad-hoc leading prefixes are normalized at logging patch stage

When adding new logs in agents:
- Prefer concise action-oriented Chinese messages
- Put changing values in message body (`key=value` or explicit text)
- Keep stage binding (`bind_task_logger(..., stage=...)`) for task context
- **强制要求：所有运行时日志输出必须为中文**。若提交英文日志，运行时会被统一规范化为中文提示。

## Core Components

### Patent Analysis Pipeline

`agents/patent_analysis/main.py` (`PatentPipeline.run`) executes:
1. download
2. parse
3. transform
4. extract
5. vision
6. check
7. generate
8. search
9. render

Artifacts are resolved via `settings.get_project_paths(...)` in `config.py`.

### Office Action Reply Workflow

`agents/office_action_reply/main.py` (`create_workflow`) builds LangGraph nodes:
- document processing
- patent retrieval
- data preparation
- amendment tracking / support basis check / strategy
- dispute extraction
- verification branches (evidence/common-knowledge/top-up)
- join + report generation + final render

### Shared Subsystems

- Parsers: `agents/common/parsers/`
- Search clients (Zhihuiya): `agents/common/search_clients/`
- Retrieval service (Qwen embeddings + rerank + Milvus session mode): `agents/common/retrieval/`
- Report rendering: `agents/common/rendering/`

## Configuration Notes

Main settings are in `config.py`; keys come from `.env`.
Key groups in `.env.example`:
- app storage paths (`APP_*`)
- task storage backend (`TASK_STORAGE_BACKEND`, D1)
- LLM/VLM/OCR/Mineru/Zhihuiya
- retrieval (`QWEN_*`, `RETRIEVAL_*`)
- optional R2 object storage (`R2_*`)
- auth/quota (`AUTH_*`, `MAX_DAILY_*`)

## Testing

Current tests focus on retrieval logic:

```bash
uv run pytest tests/test_retrieval_service.py
```

## Output Artifacts

Task outputs are under `output/<workspace_id>/` (or configured `APP_OUTPUT_DIR`), typically including:
- `raw.pdf`
- `patent.json`
- `parts.json`
- `image_parts.json`
- `check.json`
- `report.json`
- `search_strategy.json`
- final `{pn}.md` and `{pn}.pdf`
