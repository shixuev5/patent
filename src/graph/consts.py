# src/graph/consts.py

# 阶段定义
PHASE_INIT = "INIT"  # 初始化：构建矩阵
PHASE_TIER1_X = "TIER1_X"  # 第一轮：精准打击 (A+B+C, A+B)
PHASE_TIER2_Y = "TIER2_Y"  # 第二轮：组合打击 (Y类构建)
PHASE_TIER3_BROAD = "TIER3_BROAD"  # 第三轮：兜底/跨领域
PHASE_DONE = "DONE"  # 结束

# 节点名称 (Node Names)
NODE_SETUP = "setup_context"  # 初始化节点
NODE_PLANNER = "strategy_planner"  # 策略生成节点
NODE_EXECUTOR = "search_executor"  # 检索执行节点
NODE_REVIEWER = "result_reviewer"  # 快速评审节点
NODE_REPORTER = "final_reporter"  # 报告生成节点
