from agents.common.patent_engines.knowledge import KnowledgeExtractor


class _StubLLM:
    def __init__(self, payload):
        self.payload = payload
        self.last_kwargs = None

    def invoke_text_json(self, **kwargs):
        self.last_kwargs = kwargs
        return self.payload


def _sample_patent_data():
    return {
        "bibliographic_data": {"abstract": "一种装置摘要"},
        "description": {
            "brief_description_of_drawings": "10-壳体；11A-定位件",
            "detailed_description": "定位件11A设置在壳体10内部。",
        },
    }


def test_knowledge_extractor_normalizes_ids_and_merges_richer_fields() -> None:
    payload = {
        "parts": [
            {
                "id": "(11A)",
                "name": "定位件",
                "function": "未提及",
                "hierarchy": "未提及",
                "spatial_connections": "位于壳体(10)内部并与导向槽配合",
                "motion_state": "保持静止",
                "attributes": "",
            },
            {
                "id": "11-a",
                "name": "定位件",
                "function": "用于对插接端进行位置约束",
                "hierarchy": "",
                "spatial_connections": "未提及",
                "motion_state": "保持静止",
                "attributes": "金属件",
            },
        ]
    }

    extractor = KnowledgeExtractor(llm_service=_StubLLM(payload), model="fake")
    parts_db = extractor.extract_entities(_sample_patent_data())

    assert set(parts_db.keys()) == {"11a"}
    record = parts_db["11a"]

    assert record["name"] == "定位件"
    assert record["function"] == "用于对插接端进行位置约束"
    assert record["hierarchy"] is None
    assert record["spatial_connections"] == "位于壳体(10)内部并与导向槽配合"
    assert record["motion_state"] == "保持静止"
    assert record["attributes"] == "金属件"


def test_knowledge_extractor_returns_empty_when_llm_schema_invalid() -> None:
    extractor = KnowledgeExtractor(llm_service=_StubLLM({"foo": []}), model="fake")

    parts_db = extractor.extract_entities(_sample_patent_data())

    assert parts_db == {}


def test_knowledge_extractor_normalizes_hierarchy_to_single_parent_id() -> None:
    payload = {
        "parts": [
            {
                "id": "10",
                "name": "驱动电机",
                "function": "提供动力",
                "hierarchy": "动力机构(100)",
                "spatial_connections": "位于壳体内",
                "motion_state": "旋转",
                "attributes": "圆柱体",
            },
            {
                "id": "11",
                "name": "固定座",
                "function": "用于安装",
                "hierarchy": "100",
                "spatial_connections": "位于底部",
                "motion_state": "静止",
                "attributes": "金属件",
            },
            {
                "id": "12",
                "name": "导向件",
                "function": "导向",
                "hierarchy": "未提及",
                "spatial_connections": "位于壳体内部",
                "motion_state": "静止",
                "attributes": "条形件",
            },
        ]
    }

    extractor = KnowledgeExtractor(llm_service=_StubLLM(payload), model="fake")
    parts_db = extractor.extract_entities(_sample_patent_data())

    assert parts_db["10"]["hierarchy"] == "100"
    assert parts_db["11"]["hierarchy"] == "100"
    assert parts_db["12"]["hierarchy"] is None


def test_knowledge_extractor_outputs_none_for_empty_text_fields() -> None:
    payload = {
        "parts": [
            {
                "id": "20",
                "name": "",
                "function": "未提及",
                "hierarchy": "未提及",
                "spatial_connections": "none",
                "motion_state": "N/A",
                "attributes": "null",
            }
        ]
    }

    extractor = KnowledgeExtractor(llm_service=_StubLLM(payload), model="fake")
    parts_db = extractor.extract_entities(_sample_patent_data())
    record = parts_db["20"]

    assert record["name"] is None
    assert record["function"] is None
    assert record["hierarchy"] is None
    assert record["spatial_connections"] is None
    assert record["motion_state"] is None
    assert record["attributes"] is None
def test_knowledge_extractor_prompt_requires_name_plus_id_in_spatial_connections() -> None:
    llm = _StubLLM({"parts": []})
    extractor = KnowledgeExtractor(llm_service=llm, model="fake")

    extractor.extract_entities(_sample_patent_data())

    system_prompt = llm.last_kwargs["messages"][0]["content"]
    assert "必须写成“部件名称 [标号]”的完整形式" in system_prompt
    assert "正确示例：" in system_prompt
    assert "错误示例：" in system_prompt
    assert "光线穿过圆孔靶标 [4] 进入平行光管A [3]" in system_prompt
    assert "光线穿过 [4] 进入 [3]" in system_prompt
