# src/graph/nodes.py

from typing import Dict, Any, List
from loguru import logger

# 引入数据结构
from src.graph.state import AgentState
from src.graph.consts import (
    PHASE_TIER1_X,
    PHASE_TIER2_Y,
    PHASE_TIER3_BROAD,
    PHASE_DONE,
)

# 引入组件
from src.graph.components.brain import StrategyBrain
from src.graph.components.hand import ExecutionHand
from src.graph.components.eye import QuickEye
from src.graph.components.reporter import SearchReporter


def setup_node(state: AgentState) -> Dict[str, Any]:
    """
    [Node] 初始化节点
    职责: 解析案情，构建 Matrix，初始化 Phase。
    """
    logger.info("--- NODE: Setup Context ---")

    # 1. 实例化 Brain
    brain = StrategyBrain(state["patent_data"], state["report_data"])

    # 2. 构建核心上下文 (Matrix)
    matrix = brain.build_search_matrix()

    # 3. 决定初始阶段
    # 默认进入 Tier 1 (找 X)，初始化迭代计数
    return {
        "search_matrix": matrix,
        "current_phase": PHASE_TIER1_X,
        "iteration_count": 0,
        "executed_queries": [],
        "executed_intents": [], # 初始化
        "found_docs": [],
        "reviewed_uids": set(), # [P1-4]
        "diff_features": [], # 初始化为空
        "validated_ipcs": [], # 初始化为空
    }


def planner_node(state: AgentState) -> Dict[str, Any]:
    """
    [Node] 策略规划节点 (Updated)
    职责: 根据当前 Phase 生成具体的检索策略 (Queries)。
    """
    phase = state["current_phase"]
    iteration = state["iteration_count"]
    # 从 State 中获取区别特征
    diff_features = state.get("diff_features", [])
    validated_ipcs = state.get("validated_ipcs", []) # 获取校准后的 IPC

    logger.info(f"--- NODE: Planner (Phase: {phase}, Iter: {iteration}) ---")

    # 如果已经结束，直接跳过
    if phase == PHASE_DONE:
        return {"planned_strategies": []}

    # 1. 实例化 Brain
    brain = StrategyBrain(state["patent_data"], state["report_data"])

    # 2. 生成策略
    # 关键修改：传入 diff_features 用于 Tier 2 的针对性规划
    strategies = brain.plan_for_phase(
        phase=phase, 
        search_matrix=state["search_matrix"], 
        executed_intents=state.get("executed_intents", []),
        executed_queries=state.get("executed_queries", []),
        diff_features=diff_features,
        validated_ipcs=validated_ipcs
    )

    if not strategies:
        logger.warning(f"Planner 生成了空策略列表 (Phase: {phase})")
    else:
        logger.info(f"Planner 生成了 {len(strategies)} 条策略")

    # 更新 State
    return {"planned_strategies": strategies}


def executor_node(state: AgentState) -> Dict[str, Any]:
    """
    [Node] 检索执行节点 (Updated Step 4)
    职责: 执行检索 -> **(新增) 自动扩展** -> 去重存储。
    """
    strategies = state["planned_strategies"]
    
    logger.info(f"--- NODE: Executor (Pending: {len(strategies)}) ---")

    if not strategies:
        return {}

    hand = ExecutionHand()

    # 1. 批量执行
    # 提取技术手段作为 Rerank 锚点
    rerank_anchor = state["report_data"].get("technical_means", "")
    
    batch_results = hand.execute_batch(
        strategies, 
        critical_date=state.get("critical_date", ""),
        rerank_anchor=rerank_anchor # [P0-2]
    )
    
    # 2. [新增] 蜘蛛扩展 (Spider Expansion)
    # 仅当本轮有结果，且结果看起来有价值时触发
    # 简单逻辑：如果有结果，就对 Top 2 进行扩展
    spider_results = []
    if batch_results:
        # 先按某种逻辑排序，这里假设 execute_batch 返回顺序大概是相关度顺序
        # 或者如果有 score 字段更好。暂时取前 2 个。
        spider_results = hand.expand_high_value_docs(batch_results[:2])

    # 合并结果
    all_new_docs = batch_results + spider_results

    # 3. 结果合并与去重 (State Management)
    # 注意：State 中的 found_docs 可能已经很大，去重效率要高
    existing_uids = {d.get("uid") or d.get("id") for d in state["found_docs"]}
    unique_new_docs = []

    for doc in all_new_docs:
        uid = doc.get("uid") or doc.get("id")
        if uid and uid not in existing_uids:
            unique_new_docs.append(doc)
            existing_uids.add(uid)

    logger.info(
        f"Executor 完成。基础命中 {len(batch_results)}，扩展命中 {len(spider_results)}。净增 {len(unique_new_docs)}。"
    )

    new_queries = [s["query"] for s in strategies if s.get("query")]
    new_intents = [s["intent"] for s in strategies if s.get("intent")]

    return {
        "found_docs": state["found_docs"] + unique_new_docs,
        "executed_queries": state["executed_queries"] + new_queries,
        "executed_intents": state.get("executed_intents", []) + new_intents, # 更新
        "planned_strategies": []
    }


def reviewer_node(state: AgentState) -> Dict[str, Any]:
    """
    [Node] 结果评审节点 (Updated Step 7)
    职责:
    1. X类判定 (QuickEye)
    2. D1 + Diff 提取
    3. [新增] Y类组合判定 (D1 + D2)
    4. 状态流转
    """
    current_phase = state["current_phase"]
    docs = state["found_docs"]
    reviewed_uids = state["reviewed_uids"]
    
    logger.info(f"--- NODE: Reviewer (Phase: {current_phase}) ---")

    eye = QuickEye(state["report_data"])

    # 1. [P1-4] 增量筛选：只看未审阅的文档
    # 优先看 Rerank 分数高的
    unreviewed_docs = [
        d for d in docs 
        if (d.get("uid") or d.get("id")) not in reviewed_uids
    ]
    # 按 Rerank Score 排序
    unreviewed_docs.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    
    candidates = unreviewed_docs[:5] # 只看 Top 5
    logger.info(f"Reviewing {len(candidates)} new candidates (Total unreviewed: {len(unreviewed_docs)})...")

    # 2. 执行阅卷
    scan_result = eye.quick_screen(candidates, top_k=5)
    
    # 3. 更新 reviewed_uids
    new_reviewed_uids = reviewed_uids.copy()
    for d in candidates:
        uid = d.get("uid") or d.get("id")
        if uid: new_reviewed_uids.add(uid)

    # 4. [P0-3] 学习机制 (防止污染)
    # 仅使用被 QuickEye 认为有价值的文档 (match_score > 20 或 best_doc)
    high_value_docs = [
        d for d in candidates 
        if d.get("analysis", {}).get("match_score", 0) > 20
    ]
    if scan_result.get("best_doc"):
        high_value_docs.append(scan_result["best_doc"])
    
    # 去重
    high_value_docs = [dict(t) for t in {tuple(d.items()) for d in high_value_docs}]

    # 执行 Harvest & Calibration
    hand = ExecutionHand() # 工具实例化
    updated_matrix = state["search_matrix"]
    validated_ipcs = state.get("validated_ipcs", [])

    if high_value_docs:
        logger.info(f"Learning from {len(high_value_docs)} validated docs...")
        
        # Keyword Harvesting
        updated_matrix = hand.harvest_new_keywords(
            docs=high_value_docs,
            current_matrix=state["search_matrix"]
        )
        
        # IPC Calibration
        new_ipcs = hand.analyze_ipcs(high_value_docs)
        if new_ipcs:
            validated_ipcs = list(set(validated_ipcs + new_ipcs))[:8]

    update_dict = {
        "best_evidence": scan_result.get("best_doc"),
        "diff_features": scan_result.get("missing_features", []),
        "reviewed_uids": new_reviewed_uids,
        "search_matrix": updated_matrix,  # 更新 Matrix
        "validated_ipcs": validated_ipcs  # 更新 IPC
    }

    # 如果找到 X，直接结束
    if scan_result["found_x"]:
        logger.success(">>> 评审通过：发现 X 类证据！准备结束检索。")
        update_dict["current_phase"] = PHASE_DONE
        return update_dict

    # 2. [Step 7 New] Y 类组合判定 (Puzzle Logic)
    # 触发条件: 当前在 Tier 2 (刚跑完针对性检索) 且 有 D1 且 有缺失特征
    best_combination = None
    has_d1 = scan_result.get("best_doc") is not None
    missing_feats = scan_result.get("missing_features", [])
    
    if current_phase == PHASE_TIER2_Y and has_d1 and missing_feats:
        d1_doc = scan_result["best_doc"]
        logger.info(f"Reviewer: 尝试寻找 D1 ({d1_doc['uid']}) 的补全证据 D2...")
        
        # 筛选候选 D2: 排除 D1 本身，优先看本轮新增的文档
        # 这里简单遍历 Top 10 非 D1 文档
        candidates = [d for d in docs if d['uid'] != d1_doc['uid']][:10]
        
        # 针对每个缺失特征，寻找 D2
        # 简化逻辑：只找第一个缺失特征的 D2 (通常解决主要区别特征就够了)
        target_feat = missing_feats[0] 
        
        for cand in candidates:
            # 只有当文档来源是针对性检索时才大概率是 D2
            # 或者我们直接暴力查
            check_res = eye.check_secondary_reference(cand, target_feat)
            
            if check_res.get("is_disclosed"):
                logger.success(f">>> 拼图成功！D1({d1_doc['uid']}) + D2({cand['uid']}) 覆盖特征 [{target_feat}]")
                best_combination = {
                    "d1": d1_doc,
                    "d2": cand,
                    "feature": target_feat,
                    "evidence": check_res.get("evidence"),
                    "reason": check_res.get("reasoning")
                }
                break # 找到一组即可
    
    if best_combination:
        update_dict["best_combination"] = best_combination
        # 找到有力组合后，可以选择结束，或者继续跑完 Tier 3
        # 模拟审查员：如果组合很强，直接结束
        logger.info("已形成完整证据链 (Y-Combination)，提前结束检索。")
        update_dict["current_phase"] = PHASE_DONE
        return update_dict

    # 3. 状态流转 (同前)
    next_phase = current_phase
    if current_phase == PHASE_TIER1_X:
        if has_d1 and missing_feats:
            next_phase = PHASE_TIER2_Y # 进 Tier 2 找 D2
        elif not has_d1 and len(docs) > 0 and state["iteration_count"] < state["max_iterations"] - 1:
            # [FIX] 如果没找到 D1，但本轮有命中(可能Matrix进化了)，再试一次 Tier 1
            logger.info("Tier 1 未命中强相关 D1，但 Matrix 已更新，保持 Tier 1 进行精炼...")
            next_phase = PHASE_TIER1_X 
        else:
            next_phase = PHASE_TIER2_Y # 进 Tier 2 泛化

    elif current_phase == PHASE_TIER2_Y:
        # 没拼成图，也没找到 X，继续 Tier 3
        next_phase = PHASE_TIER3_BROAD

    elif current_phase == PHASE_TIER3_BROAD:
        next_phase = PHASE_DONE

    new_iteration = state["iteration_count"] + 1
    if new_iteration >= state.get("max_iterations", 3):
        next_phase = PHASE_DONE

    update_dict["current_phase"] = next_phase
    update_dict["iteration_count"] = new_iteration

    return update_dict


def reporter_node(state: AgentState) -> Dict[str, Any]:
    """
    [Node] 报告生成节点 (Updated Step 9)
    职责: 调用 Reporter 组件，渲染最终交付物。
    """
    logger.info("--- NODE: Final Reporter ---")

    reporter = SearchReporter(state)
    final_output = reporter.generate_final_output()

    # 打印简要日志
    metrics = final_output["metrics"]
    logger.success(f"Report Generated. Docs: {metrics['total_hits']}, Queries: {metrics['total_queries']}")
    
    if state.get("best_evidence") and state["best_evidence"].get("is_x_class"):
         logger.success(f"Final Outcome: [X] Novelty Destroyed by {state['best_evidence'].get('pn')}")
    elif state.get("best_combination"):
         logger.success(f"Final Outcome: [Y] Combination Found ({state['best_combination']['d1']['pn']} + {state['best_combination']['d2']['pn']})")
    else:
         logger.info("Final Outcome: [A] Background Art Only")

    return {"final_report": final_output}