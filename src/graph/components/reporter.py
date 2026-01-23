# src/graph/components/reporter.py

import json
from typing import Dict, List, Any
from src.graph.state import AgentState

class SearchReporter:
    """
    报告生成组件 (Step 9 New):
    职责: 将 Graph 运行结果转化为符合审查规范的报告。
    """

    def __init__(self, state: AgentState):
        self.state = state
        self.patent_data = state["patent_data"]
        self.report_data = state["report_data"]

    def generate_final_output(self) -> Dict[str, Any]:
        """生成最终的结构化输出"""
        return {
            "search_log": self._generate_search_log(),
            "relevant_docs": self._format_relevant_docs(),
            "examination_logic": self._generate_examination_logic(),
            "metrics": {
                "total_queries": len(self.state["executed_queries"]),
                "total_hits": len(self.state["found_docs"]),
                "phases_completed": self.state["current_phase"],
                "calibrated_ipcs": self.state.get("validated_ipcs", [])
            }
        }

    def _generate_search_log(self) -> str:
        """生成检索历史摘要 (Table-like Markdown)"""
        strategies = self.state.get("planned_strategies", []) 
        # 注意: state['planned_strategies'] 在执行后会被清空，
        # 实际应该记录在 executed_queries 或专门的 history list 里。
        # 这里假设 executed_queries 记录了 query string，或者我们需要在 state 里增加一个 detailed_history。
        # 简单起见，我们列出 executed_queries 的前 10 条和 IPC 校准结果。
        
        log_lines = [
            "### 检索策略记录",
            f"- **查新日期**: {self.state.get('critical_date')}",
            f"- **最终使用分类号**: {', '.join(self.state.get('validated_ipcs', []) or ['未校准'])}",
            "- **执行语句示例**:"
        ]
        
        # 取最后几条去重后的 Query
        unique_queries = list(set(self.state["executed_queries"]))[-5:]
        for q in unique_queries:
            log_lines.append(f"  - `{q}`")
            
        return "\n".join(log_lines)

    def _format_relevant_docs(self) -> List[Dict]:
        """格式化相关文档列表 (X/Y/A/E/P)"""
        # 提取 Top Docs (按 Score 或 X/Y 标记)
        raw_docs = self.state["found_docs"]
        
        # 优先级排序: Best Evidence > Combination D2 > X-Class > Others
        formatted = []
        
        # 辅助: 标记 D1 和 D2 的 ID
        best_ev = self.state.get("best_evidence") or {}
        best_combo = self.state.get("best_combination") or {}
        
        d1_id = best_ev.get("uid") or best_combo.get("d1", {}).get("uid")
        d2_id = best_combo.get("d2", {}).get("uid")

        seen_ids = set()

        # 1. 处理 Best Evidence / D1
        if d1_id:
            d1_doc = next((d for d in raw_docs if d.get("uid") == d1_id), best_ev)
            if d1_doc:
                formatted.append(self._enrich_doc_display(d1_doc, "X" if d1_doc.get("is_x_class") else "Y1/D1"))
                seen_ids.add(d1_id)

        # 2. 处理 D2
        if d2_id:
            d2_doc = next((d for d in raw_docs if d.get("uid") == d2_id), best_combo.get("d2"))
            if d2_doc:
                formatted.append(self._enrich_doc_display(d2_doc, "Y2/D2"))
                seen_ids.add(d2_id)

        # 3. 其他高分文档 (A类)
        # 过滤掉 D1/D2，按分数排
        others = sorted([d for d in raw_docs if d.get("uid") not in seen_ids], 
                        key=lambda x: x.get("score", 0), reverse=True)
        
        for doc in others[:5]: # 只列前5
            # 判断是否 E/P
            date_logic = doc.get("check_date_logic", "")
            doc_type = "E" if date_logic == "Conflicting" else "A"
            formatted.append(self._enrich_doc_display(doc, doc_type))

        return formatted

    def _enrich_doc_display(self, doc: Dict, doc_type: str) -> Dict:
        """为输出文档增加展示字段"""
        return {
            "type": doc_type, # X, Y, A, E, P
            "pn": doc.get("pn") or doc.get("id"),
            "title": doc.get("title"),
            "score": doc.get("score"),
            "reason": doc.get("analysis", {}).get("match_score"), # 粗略理由
            "claim_chart": doc.get("analysis", {}).get("claim_chart", []) # 关键数据
        }

    def _generate_examination_logic(self) -> str:
        """
        生成审查意见评述草稿 (The Core Value)
        """
        best_ev = self.state.get("best_evidence")
        best_combo = self.state.get("best_combination")
        diff_features = self.state.get("diff_features", [])

        lines = ["### 审查评述建议"]

        # Scene 1: 发现了 X 类
        if best_ev and best_ev.get("is_x_class"):
            lines.append(f"**【新颖性 (A.22.2) 评述】**")
            lines.append(f"对比文件 1 ({best_ev.get('pn', 'CNxxxxx')}) 公开了全部权利要求特征。")
            lines.append("具体特征对应关系如下：")
            
            chart = best_ev.get("analysis", {}).get("claim_chart", [])
            for item in chart:
                status = "已公开" if item.get("status") == "disclosed" else "争议"
                lines.append(f"- {item.get('feature_name')}: {status} (证据: {item.get('evidence', '...')})")
        
        # Scene 2: 发现了 Y 类组合 (D1 + D2)
        elif best_combo:
            lines.append(f"**【创造性 (A.22.3) 评述】**")
            d1 = best_combo.get("d1", {})
            d2 = best_combo.get("d2", {})
            feature = best_combo.get("feature", "特定特征")
            
            lines.append(f"1. 对比文件 1 ({d1.get('pn')}) 公开了除【{feature}】以外的大部分特征，构成了最接近的现有技术。")
            lines.append(f"2. 本申请与对比文件 1 的区别技术特征为：{feature}。")
            lines.append(f"3. 即使该特征未被 D1 公开，但对比文件 2 ({d2.get('pn')}) 在【{d2.get('title')}】中公开了该特征。")
            lines.append(f"   证据: {best_combo.get('evidence')}")
            lines.append(f"4. 且 D2 给出了将其应用于 D1 的技术启示，即：{best_combo.get('reason')}。")
            lines.append("因此，权利要求 1 不具备突出的实质性特点和显著的进步。")
            
        # Scene 3: 只有 A 类 (D1 + 区别特征)
        elif best_ev:
            lines.append(f"**【创造性 (A.22.3) 评述 - 尚缺 D2】**")
            lines.append(f"对比文件 1 ({best_ev.get('pn')}) 为最接近现有技术。")
            lines.append(f"区别特征为: {', '.join(diff_features)}。")
            lines.append("目前尚未检索到针对上述区别特征的强有力 D2 证据，建议重点审查上述特征是否属于公知常识。")
            
        else:
            lines.append("本次检索未发现密切相关的对比文件。")

        return "\n".join(lines)