"""
基于 LLM 的专利文档结构化提取器
使用大语言模型解析专利文档的 Markdown 内容，提取结构化信息
"""

import re
import json
from loguru import logger
from agents.common.utils.llm import get_llm_service
from agents.common.patent_structuring.models import PatentDocument


class LLMBasedExtractor:
    """基于 LLM 的专利文档结构化提取器"""

    def __init__(self):
        self.llm_service = get_llm_service()

    def extract(self, md_content: str) -> dict:
        """
        使用 LLM 提取专利文档的结构化信息

        Args:
            md_content: 专利文档的 Markdown 内容

        Returns:
            结构化的专利数据字典
        """
        logger.info("LLM 结构化抽取开始")

        cleaned_content = self.preprocess_patent_text(md_content)

        try:
            json_data = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": f"请解析以下专利文档：\n\n{cleaned_content}"},
                ],
                task_kind="patent_structuring_extract",
                temperature=0.0,
            )
            json_data = self._normalize_llm_json_data(json_data)

            # 使用 Pydantic 进行结构校验和类型转换
            patent_obj = PatentDocument.model_validate(json_data)

            # 转回字典
            result_dict = patent_obj.model_dump()

            logger.success(
                f"LLM 结构化抽取成功: {patent_obj.bibliographic_data.application_number}"
            )
            return result_dict

        except Exception as e:
            logger.error(f"LLM 结构化抽取失败: {e}")
            # 提供更详细的错误信息
            logger.error(f"错误类型: {type(e).__name__}")
            if 'json_data' in locals():
                logger.error(f"无效 JSON 数据: {json.dumps(json_data, ensure_ascii=False, indent=2)}")
            raise e

    @staticmethod
    def preprocess_patent_text(text: str) -> str:
        """多法域兼容的预处理清洗，降低 Token 消耗并移除干扰符"""

        text = str(text or "")

        # 1. 移除多法域常见段落号，不碰正文中的合法编号、范围或连字符
        text = re.sub(r'(?:\[|【|<)\d{4}(?:\]|】|>)\s*', '', text)

        # 2. 清理 HTML 表格标签与孤立页码/行号
        text = re.sub(r'(?i)<br\s*/?>', '\n', text)
        text = re.sub(r'(?i)</?(?:table|tr)>', '\n', text)
        text = re.sub(r'(?i)</?(?:td|th)>', ' ', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'(?m)^\s*\d+\s*$', '', text)

        # 3. 清理明显页眉/页脚噪声，但避免误删正常正文
        text = re.sub(r'(?im)^\s*EUROPEAN SEARCH REPORT\s*$', '', text)
        text = re.sub(r'(?im)^\s*ANNEX TO THE EUROPEAN SEARCH REPORT.*$', '', text)

        # 4. 压缩空行与多余空白
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r' *\n *', '\n', text)

        return text.strip()

    @staticmethod
    def _normalize_llm_json_data(json_data: dict) -> dict:
        """
        在保持核心字段严格校验的前提下，对 LLM 常见漏字段做最小归一化。
        目标是避免非核心可空字段缺失导致整体失败。
        """
        if not isinstance(json_data, dict):
            return {}

        normalized = dict(json_data)
        for key in ["bibliographic_data", "claims", "description", "drawings"]:
            if key not in normalized or normalized[key] is None:
                normalized[key] = [] if key in {"claims", "drawings"} else {}

        bibliographic = dict(normalized.get("bibliographic_data") or {})
        for field in [
            "priority_date",
            "publication_number",
            "publication_date",
            "abstract_figure",
        ]:
            if bibliographic.get(field) is None:
                bibliographic[field] = ""
        for field in ["ipc_classifications", "applicants", "inventors"]:
            if bibliographic.get(field) is None:
                bibliographic[field] = []
        if bibliographic:
            normalized["bibliographic_data"] = bibliographic

        description = dict(normalized.get("description") or {})
        for field in [
            "technical_effect",
            "brief_description_of_drawings",
            "technical_field",
            "background_art",
            "summary_of_invention",
            "detailed_description",
        ]:
            if description.get(field) is None:
                description[field] = ""
        if description:
            normalized["description"] = description

        for claim in normalized.get("claims", []):
            if not isinstance(claim.get("parent_claim_ids"), list):
                claim["parent_claim_ids"] = []
            claim["claim_id"] = str(claim.get("claim_id", ""))

        return normalized

    def _get_system_prompt(self):
        return r"""你是一个精通多法域（中、美、欧、日、韩）专利文本解析的资深AI工程师与法务专家。你的任务是将杂乱的 Markdown 专利文本解析为结构化、干净的 JSON。

### 一、多法域锚点识别指南（必须优先使用）
面对不同国家的专利，请严格寻找以下锚点来提取信息，**切勿凭借常识臆造**：
- **著录项目（INID Codes）**：(11)公开/公告号, (21)申请号, (22)申请日, (30)优先权, (51)IPC分类, (54)名称, (57)摘要, (71)/(73)申请人, (72)发明人, (74)代理机构。
- **CN (中国)**：寻找`技术领域`、`背景技术`、`发明内容`、`附图说明`、`具体实施方式`。权利要求以 `1.` 开始。
- **US (美国)**：寻找 `Pub. No.`、`Filed:`、`BACKGROUND`、`SUMMARY`、`BRIEF DESCRIPTION OF THE DRAWINGS`、`DETAILED DESCRIPTION`。权利要求以 `1.`、`2.` 并在句末有句号。
- **EP (欧洲)**：寻找 `Application number:`、`Date of publication:`、`Description`、`Claims`。若出现 `Amended claims`，必须优先提取修正后的权利要求。
- **JP (日本)**：寻找 `【特許公開番号】`、`【発明の名称】`、`【技術分野】`、`【背景技術】`、`【発明の概要】`、`【図面の簡単な説明】`。权利要求必定以 `【請求項X】` 标记。
- **KR (韩国)**：寻找 `공개번호`、`출원번호`、`발명의 설명`、`청구항 1`。权利要求常以 `# 청구항 1` 标示。

### 二、核心清洗与容错规则（禁止违反）
1. **剔除噪点**：绝不能把 OCR 扫描识别出的 `(72)`、`FIG.1`、`[0001]`、`EUROPEAN SEARCH REPORT` 等标记本身当做字段值存入 JSON。
2. **文本原样保留**：完整保留正文中的 LaTeX 公式（`$$...$$` 或 `$...$`）、化学分子式及特殊符号。
3. **转义合法性**：反斜杠必须合法转义；JSON 中的反斜杠 `\` 必须正确转义为 `\\`。

### 三、字段级强约束（Data Schema）
1. 所有字符串字段禁止返回 `null`，若原文缺失必须返回 `""`（空字符串）。
2. `applicants`：`name` 只能包含公司或个人名称；地址信息必须剥离到 `address`。如果无法确定地址，`address` 留空 `""`，**绝不可把地址混进名称**。
3. `inventors`：仅保留人名字符串数组，剔除国籍、城市、邮编等冗余信息。
4. `priority_date` / `publication_date` / `application_date`：提取并标准化为 `YYYY.MM.DD` 格式。

### 四、权利要求 (Claims) 抽取深度规则
这是最重要的部分，必须逐条准确解析：
1. `claim_text`：**必须剔除开头的编号**（如去掉"1."或"【請求項1】"），保留完整的法律条款文本。
2. `claim_type`：
   - 如果文本明确引用了其他权利要求（包含 "according to claim", "any of claims", "The system of claim 1", "請求項1に記載の", "제1항에 있어서" 等），判定为 `"dependent"`。
   - 否则为 `"independent"`。
3. `parent_claim_ids`（**逻辑展开**）：
   - 只能填写直接引用的父权项 ID（字符串数组）。
   - **必须展开范围表达式**：如 "claims 1-3" -> `["1", "2", "3"]`；"請求項1から3" -> `["1", "2", "3"]`；"1 or 2" -> `["1", "2"]`。
   - 若为独立权利要求，必须严格返回 `[]`。

### 五、说明书 (Description) 抽取边界
- `summary_of_invention` (发明内容)：仅保留技术方案。如果后半段明确出现“有益效果/发明效果/ADVANTAGEOUS EFFECTS”，请将其截断并填入 `technical_effect`。
- `brief_description_of_drawings` (附图说明)：**仅提取“部件标号说明”列表**（例如：`1-壳体、2-齿轮`）。不要提取对图纸整体画面的描述语句。

### 六、附图资源 (Drawings) 抽取规则
- `caption`：去掉图号。例如 `图1是结构示意图` -> `结构示意图`；`FIG. 2 shows a view` -> `a view`。
- `figure_label`：必须统一标准化为 `图{编号}`（如 `图1`、`图2A`），忽略原文是 FIG. 还是 図。
- 同一 `file_path` 只能绑定一个 `figure_label`。摘要附图不得混入 `drawings` 主列表。若无附图，`drawings` 也必须返回空数组 `[]`。

### 七、输出格式要求
必须返回纯 JSON 对象，不要包含 markdown 代码块包裹（如 ```json...```），或者确保能被严格解析。格式如下：
{
  "bibliographic_data": {
    "application_number": "...",
    "application_date": "...",
    "priority_date": "...",
    "publication_number": "...",
    "publication_date": "...",
    "invention_title": "...",
    "ipc_classifications": ["G01K 7/36"],
    "applicants":[{"name": "...", "address": "..."}] ,
    "inventors": ["..."],
    "agency": {"agency_name": "...", "agents": ["..."]},
    "abstract": "...",
    "abstract_figure": "..."
  },
  "claims":[
    {
      "claim_id": "1",
      "claim_text": "...",
      "claim_type": "independent",
      "parent_claim_ids": []
    },
    {
      "claim_id": "2",
      "claim_text": "...",
      "claim_type": "dependent",
      "parent_claim_ids": ["1"]
    }
  ],
  "description": {
    "technical_field": "...",
    "background_art": "...",
    "summary_of_invention": "...",
    "technical_effect": "...",
    "brief_description_of_drawings": "...",
    "detailed_description": "..."
  },
  "drawings":[
    {
      "file_path": "images/fig1.jpg",
      "figure_label": "图1",
      "caption": "..."
    }
  ]
}
"""
