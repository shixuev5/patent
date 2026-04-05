"""
外部检索公共工具
复用多引擎查询条件生成与 trace 组装逻辑。
"""

import json
import re
from typing import Any, Dict, List, TypedDict

from loguru import logger


ENGINE_ALIASES = {
    "openalex": "openalex",
    "academic": "openalex",
    "scholar": "openalex",
    "zhihuiya": "zhihuiya",
    "patent": "zhihuiya",
    "tavily": "tavily",
    "web": "tavily",
}

_ENGINE_QUERY_RULES = {
    "openalex": {
        "modes": {"boolean"},
        "intents": {"anchor", "expansion"},
    },
    "zhihuiya": {
        "modes": {"lexical", "semantic"},
        "intents": {"core_patent", "expansion"},
    },
    "tavily": {
        "modes": {"web"},
        "intents": {"reference", "technical"},
    },
}

ENGINE_HINTS = {
    "openalex": "academic paper / review / tutorial",
    "zhihuiya": "patent query-search + semantic",
    "tavily": "web reference / technical pages",
}

_BOOLEAN_OPERATOR_WORDS = {"AND", "OR", "NOT"}


class QuerySpec(TypedDict):
    text: str
    mode: str
    intent: str


def normalize_query_list(values: List[Any], limit: int = 2) -> List[str]:
    normalized: List[str] = []
    for value in values or []:
        text = " ".join(str(value).split())
        if not text or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def make_query_spec(text: Any, mode: str, intent: str) -> QuerySpec | None:
    normalized_text = " ".join(str(text or "").split())
    normalized_mode = str(mode or "").strip().lower()
    normalized_intent = str(intent or "").strip().lower()
    if not normalized_text or not normalized_mode or not normalized_intent:
        return None
    return {
        "text": normalized_text,
        "mode": normalized_mode,
        "intent": normalized_intent,
    }


def normalize_query_specs(values: List[Any], engine: str, limit: int = 2) -> List[QuerySpec]:
    normalized: List[QuerySpec] = []
    seen = set()
    rules = _ENGINE_QUERY_RULES.get(engine, {})
    allowed_modes = set(rules.get("modes", set()))
    allowed_intents = set(rules.get("intents", set()))
    for value in values or []:
        item = _to_dict(value)
        spec = make_query_spec(
            item.get("text", ""),
            item.get("mode", ""),
            item.get("intent", ""),
        )
        if not spec:
            continue
        if allowed_modes and spec["mode"] not in allowed_modes:
            continue
        if allowed_intents and spec["intent"] not in allowed_intents:
            continue
        key = (spec["text"], spec["mode"], spec["intent"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(spec)
        if len(normalized) >= limit:
            break
    return normalized


def flatten_query_texts(queries_by_engine: Dict[str, List[QuerySpec]]) -> List[str]:
    flat: List[str] = []
    for engine_queries in (queries_by_engine or {}).values():
        for query in engine_queries or []:
            text = " ".join(str(_to_dict(query).get("text", "")).split())
            if text and text not in flat:
                flat.append(text)
    return flat


def extract_must_keep_phrases(
    feature_text: str,
    claim_text: str,
    primary_queries: Dict[str, List[QuerySpec]] | None = None,
    *,
    limit: int = 8,
) -> List[str]:
    phrases: List[str] = []
    seen = set()

    def _add(value: Any):
        text = " ".join(str(value or "").split())
        if not text or text in seen:
            return
        seen.add(text)
        phrases.append(text)

    raw_texts = [feature_text, claim_text]
    if primary_queries:
        raw_texts.extend(flatten_query_texts(primary_queries))

    joined = "\n".join(str(item or "") for item in raw_texts if item)
    for match in re.findall(r"[“\"]([^”\"]{2,80})[”\"]", joined):
        _add(match)
        if len(phrases) >= limit:
            return phrases[:limit]
    for match in re.findall(r"[A-Z]{2,}[A-Z0-9-]*", joined):
        if match in _BOOLEAN_OPERATOR_WORDS:
            continue
        _add(match)
        if len(phrases) >= limit:
            return phrases[:limit]
    for match in re.findall(r"\b(?=\w*[A-Za-z])(?=\w*\d)[A-Za-z0-9-]{3,}\b", joined):
        _add(match)
        if len(phrases) >= limit:
            return phrases[:limit]

    for text in raw_texts:
        for segment in re.split(r"[，,。；;：:\n]", str(text or "")):
            cleaned = " ".join(segment.split()).strip("()[]{} ")
            if not cleaned:
                continue
            if re.search(r"[\u4e00-\u9fff]", cleaned) and 4 <= len(cleaned) <= 24:
                _add(cleaned)
            elif len(cleaned.split()) in {2, 3, 4, 5} and len(cleaned) >= 12:
                _add(cleaned)
            if len(phrases) >= limit:
                return phrases[:limit]
    return phrases[:limit]


def plan_engine_queries(
    llm_service: Any,
    user_context: Dict[str, Any],
    fallback_queries: Dict[str, List[QuerySpec]],
    scenario: str,
    per_engine_limit: int = 2,
) -> Dict[str, List[QuerySpec]]:
    
    # 使用 Markdown 结构化 Prompt，明确角色、任务、各引擎规则和输出要求
    system_prompt = f"""你是专业的专利与学术文献检索策略专家。当前所处业务场景：【{scenario}】。
你的任务是根据用户提供的专利上下文（包含特征词、权利要求、必留词等），为三个不同的搜索引擎（OpenAlex, Zhihuiya, Tavily）精准规划检索 query。

### 核心输入提取指引
请仔细阅读 User 提供的 JSON 格式 Context：
1. `feature_text` / `claim_text`: 必须作为构建检索式的核心技术基准。
2. `must_keep_phrases`: 核心技术特征，必须在主要检索式中得到保留，切勿过度泛化或被无关的同义词完全替换。

### 各引擎检索规则（严格执行）
每个引擎最多输出 {per_engine_limit} 条 Query。

#### 1. OpenAlex (学术文献/英文) -> 【极其重要：学术范式】
- **语言**：必须且仅限使用【英文】。
- **技术降维**：剥离专利的“工程结构/实现细节”，提取其背后的“科学原理/底层算法/核心机制/材料化学成分”。
- **风格**：输出 3-7 个词的【纯学术关键词组合】（空格分隔），绝对禁止输出自然语言长句或复杂的布尔嵌套。
- **词汇禁忌（绝对禁止）**：
  1. 禁止专利审查业务词：如 `common general knowledge`, `prior art`, `claim`, `distinguishing feature`。
  2. 禁止专利八股/工程泛词：如 `apparatus`, `system`, `method for`, `device`, `plurality of`, `module`。
  3. 禁止过度宽泛的同义替换：例如不能将 `Transformer attention` 宽泛改写为 `machine learning`。
- **术语跨界映射（核心考点）**：
  - 遇到中文或生硬的工程词汇时，必须将其映射为 IEEE, ACM, Nature, Elsevier 等顶级学术库中的【高频标准术语】。
  - 切忌字面直译 (Literal Translation)。例如：
    - (散热/冷却) 映射为 -> `thermal management`
    - (柔性机械臂) 映射为 -> `soft robotics` / `compliant mechanism`
    - (大模型微调) 映射为 -> `large language model finetuning` / `parameter efficient`
- **正反例**：
  - [正例]: `lithium battery thermal management phase change material`
  - [反例]: `heat dissipation apparatus device for battery method` (包含工程词，太low)
  - [正例]: `partially submerged flexible cylinder vortex induced vibration`
  - [反例]: `simulated experimental apparatus for flow induced vibration` (包含 apparatus, 且翻译生硬)
- **Query 1 (Anchor)**:
  - `mode`: "boolean" 
  - `intent`: "anchor"
  - **规则**: 提取最核心的 2-3 个学术实体词，强制包含最具区分度的技术短语，直接锁定现象、算法或对象。
- **Query 2 (Expansion)** (如有):
  - `mode`: "boolean"
  - `intent`: "expansion"
  - **规则**: 在 Anchor 的基础上，增加 1-2 个限定维度的学术词汇（如：特定的 evaluation metric, boundary condition, application scenario 等）做温和扩展。

#### 2. Zhihuiya (专利数据库/中英文)
- **词汇倾向**：偏向专利术语表达，保留工程结构词，切忌宽泛化。
- **Query 1 (Core Patent)**:
  - `mode`: "lexical"
  - `intent`: "core_patent"
  - **规则**: 使用保守的关键词短语检索式（如空格分隔的关键词组），绝对不要使用完整的自然语言句子。
- **Query 2 (Expansion)** (如有):
  - `mode`: "semantic"
  - `intent`: "expansion"
  - **规则**: 用于扩召回，可适当使用语义化表达或自然语言短语。

#### 3. Tavily (网页通用检索/中文)
- **语言**：主要使用中文。
- **策略**：围绕 `feature_text` 和 `must_keep_phrases` 组织，面向搜索引擎的 query，不要机械生硬地堆砌词汇（如直接拼接“教材 手册”）。
- **Query 1 (Reference)**:
  - `mode`: "web"
  - `intent`: "reference"
  - **规则**: 检索偏基础、权威、可用于说明通用技术知识的资料（如教科书、国标规范、白皮书术语）。
- **Query 2 (Technical)** (如有):
  - `mode`: "web"
  - `intent`: "technical"
  - **规则**: 检索偏向具体的实现细节、技术公开、产品文档或行业工程实践资料。

### 输出格式要求
你必须且只能输出合法的 JSON 对象，不要包含任何 Markdown 标记（如 ```json ），也不要任何解释性文本。
严格遵循以下 JSON Schema：
{{
  "openalex": [
    {{"text": "...", "mode": "boolean", "intent": "anchor"}},
    {{"text": "...", "mode": "boolean", "intent": "expansion"}}
  ],
  "zhihuiya": [
    {{"text": "...", "mode": "lexical", "intent": "core_patent"}},
    {{"text": "...", "mode": "semantic", "intent": "expansion"}}
  ],
  "tavily": [
    {{"text": "...", "mode": "web", "intent": "reference"}},
    {{"text": "...", "mode": "web", "intent": "technical"}}
  ]
}}"""

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": json.dumps(user_context, ensure_ascii=False),
        },
    ]
    
    try:
        response = llm_service.invoke_text_json(
            messages=messages,
            task_kind="retrieval_query_planning",
            temperature=0.1,  # 保持低温度以保证JSON格式和逻辑的稳定性
        )
        parsed = _to_dict(response)
        normalized: Dict[str, List[QuerySpec]] = {"openalex": [], "zhihuiya": [], "tavily": []}
        for key, value in parsed.items():
            engine = ENGINE_ALIASES.get(str(key).strip().lower())
            if engine and isinstance(value, list):
                normalized[engine] = normalize_query_specs(value, engine=engine, limit=per_engine_limit)
        
        # 校验：确保至少有一个引擎生成了有效的 query
        if any(normalized.values()):
            return normalized
            
    except Exception as ex:
        logger.warning(f"LLM 生成检索条件失败，将使用规则兜底: {ex}")

    normalized_fallback: Dict[str, List[QuerySpec]] = {"openalex": [], "zhihuiya": [], "tavily": []}
    for key, value in (fallback_queries or {}).items():
        engine = ENGINE_ALIASES.get(str(key).strip().lower())
        if engine and isinstance(value, list):
            normalized_fallback[engine] = normalize_query_specs(value, engine=engine, limit=per_engine_limit)
    return normalized_fallback

def build_trace_retrieval(
    queries_by_engine: Dict[str, List[QuerySpec]],
    retrieval_engines: List[str],
    retrieval_meta: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    retrieval = _to_dict(retrieval_meta).get("retrieval", {})
    if isinstance(retrieval, dict) and retrieval:
        return retrieval

    fallback: Dict[str, Dict[str, Any]] = {}
    for engine in retrieval_engines:
        fallback[engine] = {
            "queries": queries_by_engine.get(engine, []),
            "filters": {},
            "result_count": 0,
            "results": [],
        }
    return fallback


def _to_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "dict"):
        return item.dict()
    return {}
