# src/graph/entrypoint.py

from pathlib import Path
from typing import Dict, Tuple
from loguru import logger

from src.graph.consts import PHASE_INIT

from src.graph.workflow import build_search_graph


def _calculate_critical_date(patent_data: Dict) -> str:
    """
    计算查新截止日期 (Critical Date)。
    逻辑：有优先权取优先权日，否则取申请日。
    返回格式: 'YYYYMMDD' (例如 '20230519')
    """
    biblio = patent_data.get("bibliographic_data", {})

    # 1. 获取日期字符串
    raw_date = biblio.get("priority_date") or biblio.get("application_date")
    if not raw_date:
        return ""

    # 2. 格式化日期 (移除点号/横杠，转为 YYYYMMDD)
    clean_date = raw_date.replace(".", "").replace("-", "").strip()

    # 3. 校验格式
    if not clean_date.isdigit() or len(clean_date) != 8:
        logger.warning(f"日期格式异常: {raw_date}，无法计算 Critical Date")
        return ""

    return clean_date


def run_search_graph(patent_data: Dict, report_data: Dict) -> Tuple[Dict, list]:
    """
    Graph 执行入口。

    Args:
        patent_data: 专利结构化数据
        report_data: 分析报告数据

    Returns:
        (search_strategy_json, examination_results_json)
        为了兼容旧的 Renderer，我们需要返回这两个格式的数据。
    """
    logger.info(">>> Initializing LangGraph Agent... <<<")

    # 1. 计算 Critical Date
    critical_date = _calculate_critical_date(patent_data=patent_data)

    # 2. 构造初始状态
    initial_state = {
        "patent_data": patent_data,
        "report_data": report_data,
        "critical_date": critical_date,
        "search_matrix": [],  # 由 setup_node 填充
        "current_phase": PHASE_INIT,
        "iteration_count": 0,
        "max_iterations": 3,
        "planned_strategies": [],
        "executed_queries": [],
        "executed_intents": [],
        "found_docs": [],
        "reviewed_uids": set(),         # [P1-4] 初始化为空集合
        "final_report": {},
        "best_evidence": None,
        "best_combination": None,  # Step 7
        "diff_features": [],  # Step 2
        "validated_ipcs": [],  # Step 6
    }

    # 3. 编译并运行图
    app = build_search_graph()

    # 使用 invoke 执行 (阻塞式)
    final_state = app.invoke(initial_state)

    # 4. 格式化输出 (Adapter Layer)

    # 获取 Step 9 生成的高级报告
    final_report = final_state.get("final_report", {})

    # 构造 search_strategy_json (用于前端展示矩阵和历史)
    # 我们把 final_report 中的 search_log 和 metrics 混进去
    search_strategy_output = {
        "critical_date": critical_date,
        "search_matrix": final_state.get("search_matrix", []),
        "metrics": final_report.get("metrics"),
        "search_plan": {
            "strategies": [
                # 这里可以放详细的 execution history，为了兼容旧格式暂且保留
                {
                    "name": "Search History Log",
                    "queries": [
                        {
                            "step": "Log",
                            "query": final_report.get("search_log", ""),
                            "db": "System",
                        }
                    ],
                }
            ]
        },
        "examination_logic": final_report.get(
            "examination_logic", ""
        ),  # 前端可以直接展示这段文本
    }

    # 构造 examination_results (用于前端展示文档列表)
    # 使用 Reporter 格式化好的 relevant_docs
    examination_output = final_report.get("relevant_docs", [])

    return search_strategy_output, examination_output
