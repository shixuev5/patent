"""
专利 AI 答复辅助Agent入口
使用LangGraph实现工作流编排
"""

import argparse
import os
import uuid
from pathlib import Path
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import InMemorySaver
from backend.log_context import bind_task_logger, task_log_context
from backend.logging_setup import setup_logging_utc8
from agents.ai_reply.src.state import WorkflowState, InputFile, WorkflowConfig
from agents.ai_reply.src.nodes.document_processing import DocumentProcessingNode
from agents.ai_reply.src.nodes.patent_retrieval import PatentRetrievalNode
from agents.ai_reply.src.nodes.data_preparation import DataPreparationNode
from agents.ai_reply.src.nodes.amendment_tracking import AmendmentTrackingNode
from agents.ai_reply.src.nodes.support_basis_check import SupportBasisCheckNode
from agents.ai_reply.src.nodes.amendment_strategy import AmendmentStrategyNode
from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.ai_reply.src.nodes.evidence_verification import EvidenceVerificationNode
from agents.ai_reply.src.nodes.common_knowledge_verification import CommonKnowledgeVerificationNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode
from agents.ai_reply.src.nodes.verification_join import VerificationJoinNode
from agents.ai_reply.src.nodes.rejection_drafting import RejectionDraftingNode
from agents.ai_reply.src.nodes.report_generation import ReportGenerationNode
from agents.ai_reply.src.nodes.final_report_render import FinalReportRenderNode
from agents.ai_reply.src.edges import handle_error
from agents.common.utils.serialization import item_get


from langgraph.types import RetryPolicy

def create_workflow(config: WorkflowConfig = None):
    """
    创建LangGraph工作流

    Args:
        config: 工作流配置

    Returns:
        LangGraph工作流对象
    """
    if config is None:
        config = WorkflowConfig()

    # 创建状态图
    workflow = StateGraph(WorkflowState)

    # 创建重试策略
    retry_policy = RetryPolicy(max_attempts=config.max_retries)

    # 添加节点并配置重试
    workflow.add_node("document_processing", DocumentProcessingNode(config), retry_policy=retry_policy)
    workflow.add_node("patent_retrieval", PatentRetrievalNode(config), retry_policy=retry_policy)
    workflow.add_node("data_preparation", DataPreparationNode(config), retry_policy=retry_policy)
    workflow.add_node("amendment_tracking", AmendmentTrackingNode(config), retry_policy=retry_policy)
    workflow.add_node("support_basis_check", SupportBasisCheckNode(config), retry_policy=retry_policy)
    workflow.add_node("amendment_strategy", AmendmentStrategyNode(config), retry_policy=retry_policy)
    workflow.add_node("dispute_extraction", DisputeExtractionNode(config), retry_policy=retry_policy)
    workflow.add_node("evidence_verification", EvidenceVerificationNode(config), retry_policy=retry_policy)
    workflow.add_node("common_knowledge_verification", CommonKnowledgeVerificationNode(config), retry_policy=retry_policy)
    workflow.add_node("topup_search_verification", TopupSearchVerificationNode(config), retry_policy=retry_policy)
    workflow.add_node("verification_join", VerificationJoinNode(config), retry_policy=retry_policy)
    workflow.add_node("rejection_drafting", RejectionDraftingNode(config), retry_policy=retry_policy)
    workflow.add_node("report_generation", ReportGenerationNode(config), retry_policy=retry_policy)
    workflow.add_node("final_report_render", FinalReportRenderNode(config), retry_policy=retry_policy)
    workflow.add_node("handle_error", handle_error)

    # ---------------- 边定义重构 ----------------
    workflow.set_entry_point("document_processing")

    # 1. document_processing 固定流向 patent_retrieval
    workflow.add_conditional_edges(
        "document_processing",
        lambda state: "failed" if state.status == "failed" else "patent_retrieval",
        {
            "failed": "handle_error",
            "patent_retrieval": "patent_retrieval",
        }
    )

    # 2. 通用路由工厂函数：统一错误处理逻辑
    def create_router(next_node):
        def router(state):
            if state.status == "failed":
                return "failed"
            return next_node
        return router

    def _item_get(item, key, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def route_from_dispute_extraction(state):
        if state.status == "failed":
            return "failed"

        has_document_based_dispute = False
        has_common_knowledge_dispute = False
        has_topup_tasks = bool(_item_get(state, "topup_tasks", []))

        for dispute in _item_get(state, "disputes", []) or []:
            examiner_opinion = _item_get(dispute, "examiner_opinion", {}) or {}
            dispute_type = _item_get(examiner_opinion, "type", "")
            if dispute_type in {"document_based", "mixed_basis"}:
                has_document_based_dispute = True
            if dispute_type in {"common_knowledge_based", "mixed_basis"}:
                has_common_knowledge_dispute = True

        next_nodes = []
        if has_document_based_dispute:
            next_nodes.append("evidence_verification")
        if has_common_knowledge_dispute:
            next_nodes.append("common_knowledge_verification")
        if has_topup_tasks:
            next_nodes.append("topup_search_verification")

        if not next_nodes:
            return "verification_join"
        if len(next_nodes) == 1:
            return next_nodes[0]
        return next_nodes

    def route_from_amendment_strategy(state):
        if state.status == "failed":
            return "failed"
        if _item_get(state, "early_rejection_reason", "") or _item_get(state, "added_matter_risk", False):
            return "report_generation"
        return "dispute_extraction"

    # 为所有单向节点绑定统一的条件路由
    workflow.add_conditional_edges("patent_retrieval", create_router("data_preparation"), {"failed": "handle_error", "data_preparation": "data_preparation"})
    workflow.add_conditional_edges("data_preparation", create_router("amendment_tracking"), {"failed": "handle_error", "amendment_tracking": "amendment_tracking"})
    workflow.add_conditional_edges("amendment_tracking", create_router("support_basis_check"), {"failed": "handle_error", "support_basis_check": "support_basis_check"})
    workflow.add_conditional_edges("support_basis_check", create_router("amendment_strategy"), {"failed": "handle_error", "amendment_strategy": "amendment_strategy"})
    workflow.add_conditional_edges(
        "amendment_strategy",
        route_from_amendment_strategy,
        {
            "failed": "handle_error",
            "dispute_extraction": "dispute_extraction",
            "report_generation": "report_generation",
        }
    )
    workflow.add_conditional_edges(
        "dispute_extraction",
        route_from_dispute_extraction,
        {
            "failed": "handle_error",
            "evidence_verification": "evidence_verification",
            "common_knowledge_verification": "common_knowledge_verification",
            "topup_search_verification": "topup_search_verification",
            "verification_join": "verification_join",
        }
    )

    workflow.add_edge("evidence_verification", "verification_join")
    workflow.add_edge("common_knowledge_verification", "verification_join")
    workflow.add_edge("topup_search_verification", "verification_join")
    workflow.add_conditional_edges(
        "verification_join",
        create_router("rejection_drafting"),
        {"failed": "handle_error", "rejection_drafting": "rejection_drafting"},
    )
    workflow.add_conditional_edges(
        "rejection_drafting",
        create_router("report_generation"),
        {"failed": "handle_error", "report_generation": "report_generation"},
    )

    # 报告生成后进入最终渲染节点
    workflow.add_conditional_edges(
        "report_generation",
        create_router("final_report_render"),
        {"failed": "handle_error", "final_report_render": "final_report_render"},
    )
    workflow.add_conditional_edges("final_report_render", create_router("end"), {"failed": "handle_error", "end": END})

    # 错误处理节点直接结束
    workflow.add_edge("handle_error", END)

    checkpointer = None
    if config.enable_checkpoint:
        checkpointer = config.checkpointer or InMemorySaver()

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


def build_runtime_config(task_id: str, checkpoint_ns: str = "ai_reply"):
    return {
        "configurable": {
            "thread_id": str(task_id).strip() or "oar-task",
            "checkpoint_ns": str(checkpoint_ns).strip() or "ai_reply",
        }
    }


def setup_logging(log_dir: str = None):
    """设置日志配置"""
    log_file = None
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir / "workflow.log")
    setup_logging_utc8(level="INFO", log_file=log_file, file_level="DEBUG", rotation="10 MB")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="专利 AI 答复辅助Agent")
    parser.add_argument("--task-id", help="复用既有任务ID与输出目录")
    parser.add_argument("--office-action", help="审查意见通知书文件路径 (PDF或Word格式)")
    parser.add_argument("--response", help="意见陈述书文件路径 (PDF或Word格式)")
    parser.add_argument("--claims-previous", help="上一版权利要求书文件路径 (PDF或Word格式)")
    parser.add_argument("--claims-current", help="当前最新权利要求书文件路径 (PDF或Word格式)")
    parser.add_argument("--comparison-docs", help="对比文件路径，多个文件用逗号分隔 (PDF或Word格式)")

    args = parser.parse_args()

    task_id = str(args.task_id or "").strip() or str(uuid.uuid4())[:8]

    # 设置输出目录
    output_dir = Path("output") / task_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 设置日志
    setup_logging(str(output_dir / "logs"))
    task_logger = bind_task_logger(task_id, "ai_reply", pn="-", stage="main")

    task_logger.info(f"任务ID: {task_id}")
    task_logger.info(f"输出目录: {output_dir}")

    # 收集输入文件
    input_files = []

    if args.office_action:
        file_path = args.office_action
        if not os.path.exists(file_path):
            task_logger.error(f"审查意见通知书文件不存在: {file_path}")
            return 1
        input_files.append(InputFile(
            file_path=file_path,
            file_type="office_action",
            file_name=os.path.basename(file_path)
        ))

    if args.response:
        file_path = args.response
        if not os.path.exists(file_path):
            task_logger.error(f"意见陈述书文件不存在: {file_path}")
            return 1
        input_files.append(InputFile(
            file_path=file_path,
            file_type="response",
            file_name=os.path.basename(file_path)
        ))

    if args.claims_previous:
        file_path = args.claims_previous
        if not os.path.exists(file_path):
            task_logger.error(f"上一版权利要求书文件不存在: {file_path}")
            return 1
        input_files.append(InputFile(
            file_path=file_path,
            file_type="claims_previous",
            file_name=os.path.basename(file_path)
        ))

    if args.claims_current:
        file_path = args.claims_current
        if not os.path.exists(file_path):
            task_logger.error(f"当前最新权利要求书文件不存在: {file_path}")
            return 1
        input_files.append(InputFile(
            file_path=file_path,
            file_type="claims_current",
            file_name=os.path.basename(file_path)
        ))

    if args.comparison_docs:
        comparison_docs = args.comparison_docs.split(",")
        for file_path in comparison_docs:
            file_path = file_path.strip()
            if not file_path:
                continue
            if not os.path.exists(file_path):
                task_logger.error(f"对比文件不存在: {file_path}")
                return 1
            input_files.append(InputFile(
                file_path=file_path,
                file_type="comparison_doc",
                file_name=os.path.basename(file_path)
            ))

    if not input_files:
        task_logger.error("未指定任何输入文件")
        return 1

    task_logger.info(f"输入文件数量: {len(input_files)}")

    # 初始化工作流配置
    config = WorkflowConfig(
        cache_dir=str(output_dir / ".cache"),
        pdf_parser=os.getenv("PDF_PARSER", "local"),
        enable_checkpoint=True,
        checkpointer=InMemorySaver(),
    )

    # 初始化工作流状态
    initial_state = WorkflowState(
        input_files=input_files,
        output_dir=str(output_dir),
        task_id=task_id,
        current_node="start",
        status="pending",
        progress=0.0
    )

    # 创建并运行工作流
    task_logger.info("创建LangGraph工作流")
    workflow = create_workflow(config)

    task_logger.info("开始执行工作流")
    try:
        with task_log_context(task_id, "ai_reply", pn="-"):
            result = workflow.invoke(
                initial_state,
                config=build_runtime_config(task_id, checkpoint_ns=config.checkpoint_ns),
            )
    except Exception as e:
        task_logger.error(f"工作流执行过程中出现未捕获的异常: {e}")
        task_logger.exception("异常堆栈信息")
        return 1

    # 输出执行结果
    task_logger.info("工作流执行完成")

    # 检查结果类型，处理可能的字典返回值
    if isinstance(result, dict):
        status = result.get("status", "failed")
        errors = result.get("errors", [])
        if status == "failed":
            task_logger.error(f"工作流执行失败，共 {len(errors)} 个错误")
            for error in errors:
                node_name = item_get(error, "node_name", "unknown")
                error_message = item_get(error, "error_message", str(error))
                task_logger.error(f"节点 {node_name}: {error_message}")
            return 1
    elif hasattr(result, "status"):
        task_logger.info(f"工作流执行完成，状态: {result.status}")
        if result.status == "failed":
            task_logger.error(f"工作流执行失败，共 {len(result.errors)} 个错误")
            for error in result.errors:
                task_logger.error(f"节点 {error.node_name}: {error.error_message}")
            return 1
    else:
        task_logger.error(f"未知的工作流执行结果类型: {type(result)}")
        return 1

    task_logger.success("工作流执行成功")
    task_logger.info(f"输出目录: {output_dir}")

    return 0


if __name__ == "__main__":
    exit_code = main()
    import sys
    sys.exit(exit_code)
