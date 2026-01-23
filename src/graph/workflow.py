# src/graph/workflow.py

from langgraph.graph import StateGraph, END

# 引入状态定义
from src.graph.state import AgentState

# 引入节点函数
from src.graph.nodes import (
    setup_node,
    planner_node,
    executor_node,
    reviewer_node,
    reporter_node,
)

# 引入常量
from src.graph.consts import (
    NODE_SETUP,
    NODE_PLANNER,
    NODE_EXECUTOR,
    NODE_REVIEWER,
    NODE_REPORTER
)

from src.graph.edges import should_continue


def build_search_graph():
    """
    构建并编译 LangGraph 应用
    """
    # 1. 初始化图
    workflow = StateGraph(AgentState)

    # 2. 添加节点
    workflow.add_node(NODE_SETUP, setup_node)
    workflow.add_node(NODE_PLANNER, planner_node)
    workflow.add_node(NODE_EXECUTOR, executor_node)
    workflow.add_node(NODE_REVIEWER, reviewer_node)
    workflow.add_node(NODE_REPORTER, reporter_node)

    # 3. 定义边 (Edges) - 线性部分
    workflow.set_entry_point(NODE_SETUP)  # Start -> Setup
    workflow.add_edge(NODE_SETUP, NODE_PLANNER)  # Setup -> Planner
    workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)  # Planner -> Executor
    workflow.add_edge(NODE_EXECUTOR, NODE_REVIEWER)  # Executor -> Reviewer

    # 4. 定义条件边 (Conditional Edges) - 循环部分
    # 从 Reviewer 出来后，调用 should_continue 函数决定去路
    workflow.add_conditional_edges(
        NODE_REVIEWER,  # 上游节点
        should_continue,  # 路由函数
        {
            NODE_REPORTER: NODE_REPORTER,  # 映射: 返回值 -> 下游节点名
            NODE_PLANNER: NODE_PLANNER,  # 映射: 返回值 -> 下游节点名
        },
    )

    # 5. 定义结束
    workflow.add_edge(NODE_REPORTER, END)

    # 6. 编译
    # checkpointer=None 表示不使用持久化记忆(Redis等)，仅内存运行
    app = workflow.compile()

    return app
