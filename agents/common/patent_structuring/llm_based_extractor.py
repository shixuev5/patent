"""
基于 LLM 的专利文档结构化提取器
使用大语言模型解析专利文档的 Markdown 内容，提取结构化信息
"""

import re
import json
from loguru import logger
from agents.common.utils.llm import get_llm_service
from config import Settings
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
            json_data = self.llm_service.chat_completion_json(
                model=Settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": cleaned_content},
                ],
                temperature=0.1,  # 低温度保持精确
            )

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
        """在输入给 LLM 前进行正则清洗，降低 LLM 认知负担并节约 Token"""

        # 1. 移除专利局标准的段落号，例如 [0001], [0012] 等
        text = re.sub(r'\[\d{4}\]\s*', '', text)

        return text.strip()

    def _get_system_prompt(self):
        return r"""你是一个精通专利文档结构的资深AI分析师。你的任务是将杂乱的Markdown专利文本解析为结构化、干净的JSON格式。

### 核心清洗指令 (Critical Cleaning Rules) - 必须严格执行！
1. **全局去噪 (Noise Removal)**:
   - 必须彻底删除所有类似 `[0001]`、`[0025]` 的段落编号。
   - 必须删除所有的页码（如 "第1页/共5页"）、页眉、页脚信息。
2. **权利要求清洗 (Claims Cleaning)**:
   - `claim_text` 必须去除开头的序号和标点！(例如原文是 "1. 一种装置..." 或 "2、根据权利要求1...", 提取后必须变成 "一种装置..." 和 "根据权利要求1...")。
   - `claim_id` 必须填写对应权利要求编号（字符串形式，如 "1"、"2"）。
3. **附图标记说明 (Brief Description of Drawings) 提取规则**:
   - 此字段**仅用于**提取类似 "1-定子，2-转子" 或 "101: 处理器" 的**部件标号说明**。
   - **严禁**提取 "图1是...的示意图" 这类图解说明文字。
   - 如果原文中根本没有部件标号说明列表，该字段**必须返回 null**，绝不可用图解说明文字充数。
4. **附图标题清洗 (Drawing Captions)**:
   - `drawings.caption` 必须去除开头的图号（如"图1"）及紧跟的谓语动词/连接词（如"为"、"是"、"示出"、"："）。例如原文"图1是装置结构图"，提取后必须为"装置结构图"。
5. **附图抽取一致性规则 (Drawings Consistency)**:
   - `drawings` 仅从“# 具体实施方式”章节后出现的附图区域提取，避免把摘要附图混入 `drawings`。
   - 同一 `file_path` 绝不允许对应多个 `figure_label`（一张图只能一个图号）。
   - 当附图图片数量与附图标题数量不一致时，允许同一 `figure_label` 出现多条记录（不同 `file_path`），用于表达同图号多图。
6. **公式与特殊符号 (LaTeX Rules)**:
   - 完整保留所有的 LaTeX 公式（`$$...$$` 或 `$...$`）。
   - **JSON转义铁律**: 原文中所有的 LaTeX 反斜杠 `\` 必须转义为双反斜杠 `\\` (例如 `$120 \\mathrm{{mm}}$`)。

### 字段边界识别规则
- **invention_title (标题字段)**: 对应 `(54)` 项，可能是“发明名称”/“实用新型名称”/“外观设计名称”，统一写入 `invention_title`。
- **summary_of_invention (发明内容)**: 仅保留技术方案本体，遇到"本发明的有益效果"或"技术效果"时必须截断。
- **technical_effect (有益效果)**: 单独提取"有益效果"段落，若文中未明确写出，则返回 null。
- **claim_type (权利要求类型)**: 即使内容提到其他权利要求（如"一种用于权利要求1所述装置的方法"），只要不以"根据/如权利要求X所述"开头，就是独立(independent)权利要求。

### 输出格式参考
请严格按照以下结构输出：
```json
{
  "bibliographic_data": {
    "application_number": "202310001234.5",
    "application_date": "2023.01.01",
    "priority_date": null,
    "publication_number": "CN116793681A",
    "publication_date": "2024.03.20",
    "invention_title": "一种基于磁纳米粒子法拉第磁光效应的温度测量方法（对应发明名称/实用新型名称/外观设计名称）",
    "ipc_classifications": ["G01K 7/36"],
    "applicants": [{"name": "华中科技大学", "address": "湖北省武汉市..."}] ,
    "inventors": ["张三", "李四"],
    "agency": {"agency_name": "某专利中心", "agents": ["王五"]},
    "abstract": "本发明公开了一种基于磁纳米粒子...",
    "abstract_figure": "images/xxx.jpg"
  },
  "claims": [
    {
      "claim_id": "1",
      "claim_text": "一种基于磁纳米粒子法拉第磁光效应的温度测量方法，包括...",
      "claim_type": "independent"
    },
    {
      "claim_id": "2",
      "claim_text": "根据权利要求1所述的方法，其特征在于...",
      "claim_type": "dependent"
    }
  ],
  "description": {
    "technical_field": "本发明属于纳米材料测试技术领域...",
    "background_art": "温度是反映生命活动状态的重要指标...",
    "summary_of_invention": "针对现有技术的以上缺陷，本发明提供了一种...",
    "technical_effect": "本发明使用法拉第磁光效应对温度进行测量，可以实现...",
    "brief_description_of_drawings": null,
    "detailed_description": "为了使本发明的目的、技术方案及优点更加清楚明白..."
  },
  "drawings": [
    {
      "file_path": "images/figure1.jpg",
      "figure_label": "图1",
      "caption": "法拉第磁光效应测温装置示意图"
    }
  ]
}
```
"""
