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
