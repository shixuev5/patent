from agents.common.patent_structuring.rule_based_extractor import RuleBasedExtractor
from agents.common.patent_structuring.hybrid_extractor import HybridExtractor
from agents.common.patent_structuring.llm_based_extractor import LLMBasedExtractor


def test_extract_brief_description_only_marker_entries() -> None:
    md = """
# 附图说明
[0053] 图1为成像系统结构图。
[0054] 图1的附图标记如下：
[0055] 1-单色仪、2A:扩束镜、101：处理器。
# 具体实施方式
"""
    brief = RuleBasedExtractor._extract_brief_description(md)
    assert brief == "1-单色仪、2A-扩束镜、101-处理器"


def test_extract_brief_description_supports_alpha_numeric_markers() -> None:
    md = """
# 附图说明
图1表示系统框图。
附图标记说明：
D1:预测电脑程序、D2：运作时间预测模型、N:网路、T-外部联络人。
# 具体实施方式
"""
    brief = RuleBasedExtractor._extract_brief_description(md)
    assert brief == "D1-预测电脑程序、D2-运作时间预测模型、N-网路、T-外部联络人"


def test_extract_figure_captions_supports_multiple_verbs() -> None:
    md = """
# 附图说明
图1是表示本发明的一实施方式的管理系统的方块图。
图2表示航路的一例。
图3示出取得航路及气象信息的区域的一例。
图4为风速及风向的气象图的一例。
图5：表示波高及波向的气象图的一例。
# 具体实施方式
"""
    captions = RuleBasedExtractor._extract_figure_captions(md)
    assert captions["1"] == "本发明的一实施方式的管理系统的方块图"
    assert captions["2"] == "航路的一例"
    assert captions["3"] == "取得航路及气象信息的区域的一例"
    assert captions["4"] == "风速及风向的气象图的一例"
    assert captions["5"] == "波高及波向的气象图的一例"


def test_extract_figure_captions_ignores_futu_biaoji_noise_line() -> None:
    md = """
# 附图说明
图1的附图标记如下：
图2表示航路的一例。
# 具体实施方式
"""
    captions = RuleBasedExtractor._extract_figure_captions(md)
    assert "1" not in captions
    assert captions["2"] == "航路的一例"


def test_extract_summary_and_effect_recognizes_faming_xiaoguo_heading() -> None:
    md = """
# 发明内容
[0007] 本发明是为了解决上述问题所成的发明，其课题在于可正确地预测设置在船舶上的机器的运作时间。
[0008] 本发明的上述课题是通过一种预测装置来解决。
[0017] 发明效果
[0018] 根据本发明，可正确地预测设置在船舶上的机器的运作时间。
# 附图说明
"""
    summary, effect = RuleBasedExtractor._extract_summary_and_effect(md)
    assert summary is not None and "课题在于可正确地预测" in summary
    assert effect == "根据本发明，可正确地预测设置在船舶上的机器的运作时间。"


def test_extract_brief_description_fallback_without_marker_heading() -> None:
    md = """
# 附图说明
图1表示装置结构图。
1-壳体、2A:连接件、D1：控制程序、T:外部终端。
# 具体实施方式
"""
    brief = RuleBasedExtractor._extract_brief_description(md)
    assert brief == "1-壳体、2A-连接件、D1-控制程序、T-外部终端"


def test_parse_drawings_never_binds_one_image_to_multiple_labels() -> None:
    md = """
# 附图说明
图1为结构图。
图2为流程图。
图3为结果图。
# 具体实施方式
![](images/a.jpg)
![](images/b.jpg)
图1
图2
![](images/c1.jpg)
![](images/c2.jpg)
图3
"""
    drawings = RuleBasedExtractor._parse_drawings(md)

    # 一图一号约束：每个 file_path 仅出现一个图号
    seen = {}
    for item in drawings:
        path = item["file_path"]
        label = item["figure_label"]
        if path in seen:
            assert seen[path] == label
        else:
            seen[path] = label

    # 图3允许多图
    figure3_paths = [d["file_path"] for d in drawings if d["figure_label"] == "图3"]
    assert set(figure3_paths) == {"images/c1.jpg", "images/c2.jpg"}


def test_parse_drawings_excludes_abstract_figure() -> None:
    md = """
(57)摘要
![](images/abs.jpg)
# 附图说明
图1为结构图。
# 具体实施方式
![](images/a.jpg)
图1
"""
    drawings = RuleBasedExtractor._parse_drawings(md)
    assert [d["file_path"] for d in drawings] == ["images/a.jpg"]


def test_extract_priority_date_from_30_block() -> None:
    md = """
(22) 申请日 2022.03.22
(30) 优先权数据
2021-068366 2021.04.14 JP
(71)申请人 某公司
"""
    assert RuleBasedExtractor._extract_priority_date(md) == "2021.04.14"


def test_rule_based_extractor_returns_empty_string_for_missing_string_fields() -> None:
    md = """
(21) 申请号 202310001234.5
(22) 申请日 2023.01.01
(54) 发明名称 一种装置
(57) 摘要 摘要文本
"""
    result = RuleBasedExtractor.extract(md)

    assert result["bibliographic_data"]["priority_date"] == ""
    assert result["bibliographic_data"]["publication_number"] == ""
    assert result["bibliographic_data"]["publication_date"] == ""
    assert result["bibliographic_data"]["abstract_figure"] == ""
    assert result["description"]["technical_field"] == ""
    assert result["description"]["background_art"] == ""
    assert result["description"]["summary_of_invention"] == ""
    assert result["description"]["technical_effect"] == ""
    assert result["description"]["brief_description_of_drawings"] == ""
    assert result["description"]["detailed_description"] == ""


def test_extract_inventors_split_chinese_names_by_single_space() -> None:
    md = """
(72)发明人竹内高穗 铃木修一 池田充志
"""
    assert RuleBasedExtractor._extract_inventors(md) == ["竹内高穗", "铃木修一", "池田充志"]


def test_extract_agency_removes_code_and_splits_agents() -> None:
    md = """
(74)专利代理机构北京品源专利代理有限公司11332
专利代理师吕琳 朴秀玉
"""
    agency = RuleBasedExtractor._extract_agency(md)
    assert agency == {
        "agency_name": "北京品源专利代理有限公司",
        "agents": ["吕琳", "朴秀玉"],
    }


def test_split_people_keeps_english_full_name() -> None:
    assert RuleBasedExtractor._split_people("John Smith") == ["John Smith"]


def test_extract_brief_description_full_marker_list_sample() -> None:
    md = """
# 附图说明
[0023] 图1为本发明的立体图；
[0024] 图2为本发明的侧面剖视图；
[0025] 图3为本发明的等轴立体示意图；
[0026] 图4为本发明床体的侧面剖视图；
[0027] 图5为本发明床体的半剖图；
[0028] 图6为本发明远红外理疗仪机用治疗床的放大示意图；
[0029] 图7为本发明散热风扇的俯视图；
[0030] 图8为本发明散热风扇的立体图；
[0031] 图9为本发明床垫的结构示意图；
[0032] 图10为本发明电致变色凸面镜的结构示意图；
[0033] 图11为本发明远红外发生器的爆炸图。
[0034] 图中标记：1、床体；2、侧面板；3、角度调整电机；4、远红外发生器；5、散热风扇；6、移动电机；7、底板；8、固定杆；9、移动气缸；10、固定块；11、滑轮；12、限制轨；13、丝杆；14、限制箱；101、床垫；102、驱动轴；103、从动齿轮；104、主动齿轮；106、透明柔性薄层；107、变色层；108、透明支撑层；109、电流变液；110、凸透件；111、电致变色凸面镜；112、压电片；113、第一玻璃或塑胶基材层；114、第一透明导电层；115、电致变色层；116、电解质层；117、离子储存层；118、第二透明导电层；119、第二玻璃或塑胶基材层；120、透明基板；201、侧面光源；301、保护壳；401、散热块。
# 具体实施方式
"""
    captions = RuleBasedExtractor._extract_figure_captions(md)
    brief = RuleBasedExtractor._extract_brief_description(md)

    assert len(captions) == 11
    assert brief is not None
    items = [item for item in brief.split("、") if item.strip()]
    assert len(items) == 36
    assert "401-散热块" in items


def test_extract_structured_claims_parent_claim_ids_single_parent() -> None:
    claims_section = """
1. 一种装置，包括壳体和控制器。
2. 根据权利要求1所述的装置，其特征在于，还包括传感器。
"""
    claims = RuleBasedExtractor.extract_structured_claims(claims_section)
    assert claims[0]["claim_type"] == "independent"
    assert claims[0]["parent_claim_ids"] == []
    assert claims[1]["claim_type"] == "dependent"
    assert claims[1]["parent_claim_ids"] == ["1"]


def test_extract_structured_claims_parent_claim_ids_multiple_parents() -> None:
    claims_section = """
1. 一种方法，包括步骤A。
2. 根据权利要求1或2所述的方法，其特征在于，包括步骤B。
"""
    claims = RuleBasedExtractor.extract_structured_claims(claims_section)
    assert claims[1]["parent_claim_ids"] == ["1", "2"]


def test_extract_structured_claims_parent_claim_ids_range_parents() -> None:
    claims_section = """
1. 一种系统，包括模块A。
2. 一种系统，包括模块B。
3. 一种系统，包括模块C。
4. 根据权利要求1至3任一项所述的系统，其特征在于，还包括模块D。
"""
    claims = RuleBasedExtractor.extract_structured_claims(claims_section)
    assert claims[3]["parent_claim_ids"] == ["1", "2", "3"]


def test_extract_applicants_split_inline_name_and_address() -> None:
    md = """
(71)申请人 北京市轨道交通建设管理有限公司地址100068北京市丰台区角门北京市轨道交通建设管理有限公司A107
"""
    applicants = RuleBasedExtractor._extract_applicants(md)
    assert applicants == [{
        "name": "北京市轨道交通建设管理有限公司",
        "address": "100068北京市丰台区角门北京市轨道交通建设管理有限公司A107",
    }]


def test_extract_applicants_keeps_two_line_format() -> None:
    md = """
(71)申请人 北京某科技有限公司
地址 100000北京市海淀区某路1号
"""
    applicants = RuleBasedExtractor._extract_applicants(md)
    assert applicants == [{
        "name": "北京某科技有限公司",
        "address": "100000北京市海淀区某路1号",
    }]


def test_extract_applicants_without_address() -> None:
    md = """
(71)申请人 上海某研究院
"""
    applicants = RuleBasedExtractor._extract_applicants(md)
    assert applicants == [{
        "name": "上海某研究院",
        "address": "",
    }]


def test_hybrid_check_missing_fields_requires_parent_claim_ids_for_dependent_claim() -> None:
    extractor = HybridExtractor.__new__(HybridExtractor)
    patent_data = {
        "bibliographic_data": {
            "application_number": "202310001234.5",
            "application_date": "2023.01.01",
            "invention_title": "一种装置",
            "ipc_classifications": ["G01K 7/36"],
            "applicants": [{"name": "某公司", "address": ""}],
            "inventors": ["张三"],
            "abstract": "摘要文本",
        },
        "claims": [{
            "claim_id": "2",
            "claim_text": "根据权利要求1所述的装置，其特征在于，还包括传感器。",
            "claim_type": "dependent",
            "parent_claim_ids": [],
        }],
        "description": {
            "technical_field": "技术领域",
            "background_art": "背景技术",
            "summary_of_invention": "发明内容",
            "detailed_description": "具体实施方式",
        },
    }
    missing = extractor._check_missing_fields(patent_data)
    assert "claims[0].parent_claim_ids" in missing


def test_parse_claims_prefers_ep_amended_claims_section() -> None:
    md = """
# Claims
1. Driver identification system for rail vehicles comprising an original feature set.
2. The system of claim 1, wherein a first parameter is configured.

# Amended claims in accordance with Rule 137(2) EPC.

1. Driver identification system for rail vehicles comprising:
wherein the contactless reading device is further characterized in that it is powered by train batteries.
2. The system of claim 1, wherein the driver data comprise the height of the driver.
3. The system of claim 1 or 2, wherein the cabin comprises a driver desk.
4. The system of any of the preceding claims, wherein the personal identification device comprises a smartphone.
"""
    claims = RuleBasedExtractor._parse_claims(md)
    assert [claim["claim_id"] for claim in claims] == ["1", "2", "3", "4"]
    assert "powered by train batteries" in claims[0]["claim_text"]
    assert claims[1]["claim_type"] == "dependent"
    assert claims[1]["parent_claim_ids"] == ["1"]
    assert claims[2]["parent_claim_ids"] == ["1", "2"]
    assert claims[3]["parent_claim_ids"] == ["1", "2", "3"]


def test_extract_structured_claims_supports_japanese_markers() -> None:
    claims_section = """
【特許請求の範囲】
【請求項1】
タイヤに配設されており、タイヤの変形に関わる物理量を計測するセンサ部と、
前記センサ部により計測された物理量に基づいて、経時的なタイヤの物性の変化を算出する物性変化算出部と、を備えるタイヤ劣化推定システム。
【請求項2】
前記センサ部は、歪を計測することを特徴とする請求項1に記載のタイヤ劣化推定システム。
【請求項3】
前記センサ部は、加速度を計測することを特徴とする請求項1または2に記載のタイヤ劣化推定システム。
"""
    claims = RuleBasedExtractor.extract_structured_claims(claims_section)
    assert [claim["claim_id"] for claim in claims] == ["1", "2", "3"]
    assert claims[0]["claim_type"] == "independent"
    assert claims[1]["claim_type"] == "dependent"
    assert claims[1]["parent_claim_ids"] == ["1"]
    assert claims[2]["parent_claim_ids"] == ["1", "2"]


def test_extract_structured_claims_supports_korean_markers() -> None:
    claims_section = """
# 청구항 1
철도차량의 차체 무게중심측정장치.

# 청구항 2
제1항에 있어서, 상기 높이 조절이 가능한 가변수단이 유압실린더로 이루어진 것을 특징으로 하는 철도차량의 차체 무게중심측정장치.

# 청구항 3
제1항에 있어서, 상기 하부받침판 아래에 연결수단이 구비되어 있는 것을 특징으로 하는 철도차량의 차체 무게중심측정장치.

# 청구항 4
제3항에 있어서, 상기 연결수단이 걸림핀과 지지판으로 이루어진 것을 특징으로 하는 철도차량의 차체 무게중심측정장치.
"""
    claims = RuleBasedExtractor.extract_structured_claims(claims_section)
    assert [claim["claim_id"] for claim in claims] == ["1", "2", "3", "4"]
    assert claims[0]["claim_type"] == "independent"
    assert claims[1]["parent_claim_ids"] == ["1"]
    assert claims[3]["parent_claim_ids"] == ["3"]


def test_extract_us_style_bibliographic_and_sections() -> None:
    md = """
(10) Pub. No.: US 2023/0403781 A1
(43) Pub. Date: Dec. 14, 2023
(54) DIELECTRIC BARRIER DISCHARGE PLASMA GENERATOR
(71) Applicant: ASMPT Singapore Pte. Ltd., Singapore (SG)
(72) Inventors: Jun QI, Hong Kong (CN); Hao MENG, Chengdu City (CN)
(21) Appl. No.: 18/204,586
(22) Filed: Jun. 1, 2023
(30) Foreign Application Priority Data Jun.8,2022 (CN) 202210642604.0

# (57) ABSTRACT
A dielectric barrier discharge plasma generator includes a ground electrode.

# DIELECTRIC BARRIER DISCHARGE PLASMA GENERATOR
FIELD OF THE INVENTION
The invention generally relates to plasma generation.

# BACKGROUND
Atmospheric pressure plasma has been used for many applications.

# SUMMARY OF THE INVENTION
It is thus an object of the invention to seek to provide an improved DBD plasma generator.

# DETAILED DESCRIPTION OF THE PREFERRED EMBODIMENTS OF THE INVENTION
FIG. 1 is a schematic cross-sectional view of a dielectric barrier discharge plasma generator.

1. A dielectric barrier discharge plasma generator, the plasma generator comprising a ground electrode.
2. The plasma generator according to claim 1, wherein the high voltage electrode includes a first conductive part.
"""
    result = RuleBasedExtractor.extract(md)
    assert result["bibliographic_data"]["application_number"] == "18/204,586"
    assert result["bibliographic_data"]["application_date"] == "2023.06.01"
    assert result["bibliographic_data"]["publication_number"] == "US 2023/0403781 A1"
    assert result["bibliographic_data"]["publication_date"] == "2023.12.14"
    assert result["bibliographic_data"]["priority_date"] == "2022.06.08"
    assert result["bibliographic_data"]["invention_title"] == "DIELECTRIC BARRIER DISCHARGE PLASMA GENERATOR"
    assert result["bibliographic_data"]["applicants"][0]["name"] == "ASMPT Singapore Pte. Ltd., Singapore (SG)"
    assert result["bibliographic_data"]["inventors"] == ["Jun QI", "Hao MENG"]
    assert "plasma generation" in result["description"]["technical_field"]
    assert "Atmospheric pressure plasma" in result["description"]["background_art"]
    assert "improved DBD plasma generator" in result["description"]["summary_of_invention"]
    assert "schematic cross-sectional view" in result["description"]["detailed_description"]
    assert result["claims"][1]["claim_type"] == "dependent"
    assert result["claims"][1]["parent_claim_ids"] == ["1"]


def test_extract_publication_number_supports_kind_code_variants() -> None:
    us_md = """
(11) US-10234567-B2
"""
    ep_md = """
(10) Pub. No.: EP1234567B1
"""
    spaced_md = """
(10) Pub. No.: EP 3 379 496 A1
"""
    assert RuleBasedExtractor._extract_publication_number(us_md) == "US-10234567-B2"
    assert RuleBasedExtractor._extract_publication_number(ep_md) == "EP1234567B1"
    assert RuleBasedExtractor._extract_publication_number(spaced_md) == "EP 3 379 496 A1"


def test_extract_publication_number_ignores_formula_and_axis_noise_without_header() -> None:
    md = """
(21)申请号 202310789004.1
(22)申请日 2023.06.30
(54)发明名称 光纤滑环动态检测装置
[0059] 式(11)可进一步表示为：
SEEGST算法如下所示。
轴系II、轴系III同轴。
"""
    assert RuleBasedExtractor._extract_publication_number(md) == ""


def test_extract_ipc_classifications_strips_version_suffix_and_normalizes_ocr_digits() -> None:
    md = """
(51) Int. Cl. G0IK 7/36 (2006.01); H01L 21/8242 (2013.01)
(52) U.S. Cl. G0IK 7/36 (2006.01)
"""
    assert RuleBasedExtractor._extract_ipc_classifications(md) == ["G01K 7/36", "H01L 21/8242"]


def test_extract_claims_section_prefers_actual_claim_run_over_numbered_summary_list() -> None:
    md = """
# 发明内容
1. 本发明具有结构简单的优点。
2. 本发明具有易安装的优点。

1. 一种环保PVC复合膜气密性检测装置，其特征在于，包括检测台。
2. 根据权利要求1所述的一种环保PVC复合膜气密性检测装置，其特征在于，还包括气泵。
3. 根据权利要求2所述的一种环保PVC复合膜气密性检测装置，其特征在于，还包括密封盒。

# 技术领域
[0001] 本发明属于PVC复合膜技术领域。
"""
    claims = RuleBasedExtractor._parse_claims(md)
    assert [claim["claim_id"] for claim in claims] == ["1", "2", "3"]
    assert claims[1]["claim_type"] == "dependent"
    assert claims[1]["parent_claim_ids"] == ["1"]


def test_parse_drawings_supports_chinese_subfigure_labels_and_captions() -> None:
    md = """
(57)摘要
摘要内容

# 附图说明
图3为网络结构图；图3的(a)为混合层的结构示意图；图3的(b)为多层感知机的结构示意图。

# 具体实施方式
![](images/f3a.jpg)
图3(a)
![](images/f3b.jpg)
图3(b)
"""
    drawings = RuleBasedExtractor._parse_drawings(md)
    assert [item["figure_label"] for item in drawings] == ["图3(a)", "图3(b)"]
    assert drawings[0]["caption"] == "混合层的结构示意图"
    assert drawings[1]["caption"] == "多层感知机的结构示意图"


def test_rule_extractor_normalizes_date_output_format() -> None:
    md = """
(21) Appl. No.: 18/204,586
(22) Filed: Jun. 1, 2023
(30) Foreign Application Priority Data Jun.8,2022 (CN) 202210642604.0
(10) Pub. No.: US 2023/0403781 A1
(43) Pub. Date: Dec. 14, 2023
(54) DIELECTRIC BARRIER DISCHARGE PLASMA GENERATOR
(71) Applicant: ASMPT Singapore Pte. Ltd., Singapore (SG)
(72) Inventors: Jun QI, Hong Kong (CN)
# (57) ABSTRACT
abstract
1. A dielectric barrier discharge plasma generator.
"""
    result = RuleBasedExtractor.extract(md)
    assert result["bibliographic_data"]["application_date"] == "2023.06.01"
    assert result["bibliographic_data"]["priority_date"] == "2022.06.08"
    assert result["bibliographic_data"]["publication_date"] == "2023.12.14"


def test_extract_figure_captions_and_drawings_support_english_labels() -> None:
    md = """
(57) ABSTRACT
Short abstract.
![](images/abstract.jpg)

![](images/f1.jpg)
FIG. 1
![](images/f2.jpg)
FIG. 2B

# BRIEF DESCRIPTION OF THE DRAWINGS
FIG. 1 is a schematic cross-sectional view of a dielectric barrier discharge plasma generator.
FIG. 2B shows a perspective view of a high voltage electrode and a resiliently deformable mechanism.

# DETAILED DESCRIPTION OF THE PREFERRED EMBODIMENTS OF THE INVENTION
Embodiments of the present invention are described below.
"""
    captions = RuleBasedExtractor._extract_figure_captions(md)
    drawings = RuleBasedExtractor._parse_drawings(md)
    assert captions["1"] == "a schematic cross-sectional view of a dielectric barrier discharge plasma generator"
    assert captions["2B"] == "a perspective view of a high voltage electrode and a resiliently deformable mechanism"
    assert [item["file_path"] for item in drawings] == ["images/f1.jpg", "images/f2.jpg"]
    assert [item["figure_label"] for item in drawings] == ["图1", "图2B"]


def test_hybrid_quality_issues_detects_polluted_multilingual_result() -> None:
    extractor = HybridExtractor.__new__(HybridExtractor)
    md = """
(57) ABSTRACT
Abstract text exists.
# BACKGROUND
Background text exists.
"""
    patent_data = {
        "bibliographic_data": {
            "application_number": "123",
            "application_date": "2023.01.01",
            "invention_title": "(72) Inventors: John Smith",
            "ipc_classifications": ["G01M 17/02"],
            "applicants": [
                {"name": "(74)代理人100105924", "address": ""},
                {"name": "【請求項1】", "address": ""},
                {"name": "Publication Classification", "address": ""},
                {"name": "FIG. 1", "address": ""},
                {"name": "청구항 1", "address": ""},
                {"name": "Applicant spillover", "address": ""},
            ],
            "inventors": ["John Smith"],
            "abstract": "",
        },
        "claims": [
            {
                "claim_id": "1",
                "claim_text": "A system ![](images/a.jpg)",
                "claim_type": "independent",
                "parent_claim_ids": [],
            }
        ],
        "description": {
            "technical_field": "",
            "background_art": "Background text exists.",
            "summary_of_invention": "",
            "detailed_description": "",
        },
    }
    issues = extractor._check_quality_issues(md, patent_data)
    assert "bibliographic_data.invention_title.polluted" in issues
    assert "bibliographic_data.applicants.abnormally_many" in issues
    assert "bibliographic_data.abstract.missing_despite_marker" in issues
    assert "claims[0].contains_unprocessed_image" in issues


def test_hybrid_quality_issues_detects_numeric_and_address_like_bibliographic_noise() -> None:
    extractor = HybridExtractor.__new__(HybridExtractor)
    issues = extractor._check_quality_issues(
        "(57) ABSTRACT\ntext",
        {
            "bibliographic_data": {
                "application_number": "x",
                "application_date": "2020.01.01",
                "invention_title": "Title",
                "ipc_classifications": ["A01B 1/00"],
                "applicants": [
                    {"name": "000003148", "address": ""},
                    {"name": "兵庫県伊丹市藤ノ木2丁目2番13号", "address": ""},
                ],
                "inventors": ["Name"],
                "agency": {"agency_name": "", "agents": ["100105924"]},
                "abstract": "ok",
            },
            "claims": [],
            "description": {},
        },
    )
    assert "bibliographic_data.applicants[0].name.invalid_chars" in issues
    assert "bibliographic_data.applicants[1].name.is_actually_address" in issues
    assert "bibliographic_data.agency.agents[0].invalid_chars" in issues


def test_llm_prompt_mentions_multijurisdictional_formats() -> None:
    prompt = LLMBasedExtractor.__new__(LLMBasedExtractor)._get_system_prompt()
    assert "中、美、欧、日、韩" in prompt
    assert "DETAILED DESCRIPTION" in prompt
    assert "【請求項1】" in prompt
    assert "청구항 1" in prompt
    assert "Amended claims" in prompt


def test_llm_prompt_preserves_strict_field_and_drawing_constraints() -> None:
    prompt = LLMBasedExtractor.__new__(LLMBasedExtractor)._get_system_prompt()
    assert "所有字符串字段禁止返回 `null`" in prompt
    assert "application_date" in prompt
    assert "同一 `file_path` 只能绑定一个 `figure_label`" in prompt
    assert "摘要附图不得混入 `drawings` 主列表" in prompt
    assert "反斜杠必须合法转义" in prompt
    assert "The system of claim 1" in prompt
    assert "`drawings` 也必须返回空数组 `[]`" in prompt


def test_llm_preprocess_preserves_meaningful_hyphens_and_ranges() -> None:
    text = """
EP 3 379 496 A1
US 2023/0403781 A1
claims 1-3
[0001]
<0002>
【0003】
"""
    cleaned = LLMBasedExtractor.preprocess_patent_text(text)
    assert "EP 3 379 496 A1" in cleaned
    assert "US 2023/0403781 A1" in cleaned
    assert "claims 1-3" in cleaned
    assert "[0001]" not in cleaned
    assert "<0002>" not in cleaned
    assert "【0003】" not in cleaned


def test_llm_json_normalizer_fills_optional_missing_fields() -> None:
    extractor = LLMBasedExtractor.__new__(LLMBasedExtractor)
    normalized = extractor._normalize_llm_json_data(
        {
            "bibliographic_data": {
                "application_number": "123",
                "application_date": "2023.01.01",
                "invention_title": "Title",
                "ipc_classifications": [],
                "applicants": [],
                "inventors": [],
                "abstract": "abs",
                "abstract_figure": None,
            },
            "claims": [],
            "description": {
                "technical_field": "",
                "background_art": "",
                "summary_of_invention": "",
                "detailed_description": "",
                "technical_effect": None,
                "brief_description_of_drawings": None,
            },
        }
    )
    assert normalized["drawings"] == []
    assert normalized["bibliographic_data"]["abstract_figure"] == ""
    assert normalized["description"]["technical_effect"] == ""
    assert normalized["description"]["brief_description_of_drawings"] == ""
