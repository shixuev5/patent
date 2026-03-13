# AI Review Agent

独立的 AI 审查流水线，优先复用 R2 中的 `analysis/<pn>.json`（`parts`/`image_parts`），命中则直接执行 AI 审查与渲染；未命中则回退到下载/解析/结构化/提取/视觉流程。
