from agents.common.patent_structuring.rule_based_extractor import RuleBasedExtractor


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
