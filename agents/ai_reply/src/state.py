"""
工作流状态管理
定义LangGraph工作流的状态结构，包含所有输入输出数据
"""

from typing import List, Optional, Dict, Any, Annotated, Union
from pydantic import BaseModel, Field
import operator


class InputFile(BaseModel):
    """输入文件信息"""
    file_path: str = Field(..., description="文件路径")
    file_type: str = Field(..., description="文件类型: office_action, response, claims_previous, claims_current, comparison_doc")
    file_name: str = Field(..., description="文件名")


class ParsedFile(BaseModel):
    """解析后的文件信息"""
    file_path: str = Field(..., description="原始文件路径")
    file_type: str = Field(..., description="文件类型")
    markdown_path: str = Field(..., description="解析后的markdown文件路径")
    content: str = Field("", description="markdown内容")


class SupportingDocCitation(BaseModel):
    """审查员观点中的文献-引用关联"""
    doc_id: str = Field(..., description="对比文件编号，如 D1")
    cited_text: str = Field("", description="审查员用于支撑观点的原文描述或关键短语")


class ExaminerOpinion(BaseModel):
    """审查员观点"""
    type: str = Field(
        ...,
        description="观点类型: document_based 或 common_knowledge_based 或 mixed_basis",
    )
    supporting_docs: List[SupportingDocCitation] = Field(
        default_factory=list,
        description="支撑文献关联信息，每项包含 doc_id/cited_text，用于定位原文上下文",
    )
    reasoning: str = Field("", description="审查员理由")


class ApplicantOpinion(BaseModel):
    """申请人观点"""
    type: str = Field(..., description="反驳类型: fact_dispute 或 logic_dispute")
    reasoning: str = Field("", description="反驳理由")
    core_conflict: str = Field("", description="核心冲突点")


class Dispute(BaseModel):
    """争辩焦点数据"""
    dispute_id: str = Field(..., description="争议项唯一标识")
    origin: str = Field("response_dispute", description="争议来源: response_dispute/amendment_review")
    source_argument_id: str = Field("", description="来源申请人论点编号")
    source_feature_id: str = Field("", description="若来自权利要求修改评判，则记录对应 feature_id")
    claim_ids: List[str] = Field(default_factory=list, description="关联权利要求序号列表")
    feature_text: str = Field(..., description="特征描述文本")
    examiner_opinion: ExaminerOpinion = Field(..., description="审查员观点")
    applicant_opinion: ApplicantOpinion = Field(..., description="申请人观点")

class EvidenceAssessmentDecision(BaseModel):
    """事实核查裁决"""
    verdict: str = Field(..., description="裁决结果: APPLICANT_CORRECT/EXAMINER_CORRECT/INCONCLUSIVE")
    reasoning: str = Field(default="", description="判断理由")
    confidence: float = Field(0.0, description="置信度 0~1")
    examiner_rejection_rationale: str = Field(
        ...,
        description="当 verdict=APPLICANT_CORRECT 时必须给出基于当前证据的替代性驳回逻辑要点；其他情况为空字符串",
    )


class EvidenceAssessmentQuote(BaseModel):
    """证据引用项"""
    doc_id: str = Field(..., description="对比文件编号，如 D1")
    quote: str = Field(default="", description="证据原文片段")
    location: str = Field(default="", description="证据位置描述")
    analysis: str = Field(default="", description="证据与争议关系分析")
    source_url: Optional[str] = Field(None, description="证据来源链接")
    source_title: Optional[str] = Field(None, description="证据来源标题")
    source_type: Optional[str] = Field(None, description="证据来源类型，如 google_scholar/google_patents/google_web/model_knowledge")


class RetrievalResultItem(BaseModel):
    """外部检索结果项（trace记录）"""
    doc_id: Optional[str] = Field(None, description="证据编号，如 EXT1")
    source_type: str = Field(default="", description="来源类型")
    title: str = Field(default="", description="标题")
    url: Optional[str] = Field(None, description="链接")
    published: Optional[str] = Field(None, description="公开日期")
    similarity_score: Optional[float] = Field(None, description="相似度分值")


class RetrievalEngineTrace(BaseModel):
    """单检索引擎追踪信息"""
    queries: List[str] = Field(default_factory=list, description="该引擎实际执行的查询条件")
    filters: Dict[str, Any] = Field(default_factory=dict, description="该引擎执行过滤条件")
    result_count: int = Field(0, description="该引擎入选证据条数")
    results: List[RetrievalResultItem] = Field(default_factory=list, description="该引擎入选证据摘要")


class EvidenceAssessmentTrace(BaseModel):
    """事实核查追踪信息"""
    used_doc_ids: List[str] = Field(default_factory=list, description="本次核查使用的对比文件编号")
    missing_doc_ids: List[str] = Field(default_factory=list, description="缺失或无有效内容的对比文件编号")
    local_retrieval: Dict[str, Any] = Field(
        default_factory=dict,
        description="本地检索追踪信息：queries/doc_filters/hit_chunks/selected_cards 等",
    )
    retrieval: Dict[str, RetrievalEngineTrace] = Field(
        default_factory=dict,
        description="按检索引擎维度记录查询条件、过滤规则与结果摘要",
    )


class EvidenceAssessment(BaseModel):
    """事实核查结果（新结构）"""
    dispute_id: str = Field(..., description="争议项唯一标识")
    origin: str = Field("response_dispute", description="核查结果来源: response_dispute/amendment_review")
    source_argument_id: str = Field("", description="来源申请人论点编号")
    source_feature_id: str = Field("", description="若来自权利要求修改评判，则记录对应 feature_id")
    claim_ids: List[str] = Field(default_factory=list, description="关联权利要求序号列表")
    claim_text: str = Field(default="", description="原权利要求文本")
    feature_text: str = Field(default="", description="争议技术特征")
    examiner_opinion: ExaminerOpinion = Field(..., description="审查员观点")
    applicant_opinion: ApplicantOpinion = Field(..., description="申请人观点")
    assessment: EvidenceAssessmentDecision = Field(..., description="核查裁决")
    evidence: List[EvidenceAssessmentQuote] = Field(default_factory=list, description="核查证据列表")
    trace: EvidenceAssessmentTrace = Field(default_factory=EvidenceAssessmentTrace, description="核查追踪信息")


class StructuredClaim(BaseModel):
    """结构化权利要求"""
    claim_id: str = Field(..., description="权利要求编号")
    claim_text: str = Field(..., description="权利要求文本")
    claim_type: str = Field("unknown", description="权利要求类型：independent/dependent/unknown")
    parent_claim_ids: List[str] = Field(default_factory=list, description="直接父权利要求编号列表")


class AddedFeature(BaseModel):
    """新增特征"""
    feature_id: str = Field(..., description="新增特征编号，如 New_F1")
    feature_text: str = Field(..., description="新增特征文本")
    feature_before_text: str = Field("", description="该变更项对应的旧版本特征片段")
    feature_after_text: str = Field("", description="该变更项对应的新版本特征片段")
    target_claim_ids: List[str] = Field(default_factory=list, description="目标权利要求编号列表")
    source_type: str = Field("spec", description="来源类型：claim/spec")
    source_claim_ids: List[str] = Field(default_factory=list, description="若来自原权利要求，则记录来源权利要求编号")


class SupportFinding(BaseModel):
    """修改支持依据核查结果"""
    feature_id: str = Field(..., description="新增特征编号")
    feature_text: str = Field(..., description="新增特征文本")
    reasoning: Optional[str] = Field(default="", description="大模型的判断推理过程")
    support_found: bool = Field(False, description="是否找到原始支持依据")
    support_basis: str = Field("", description="支持依据描述")
    risk: str = Field("", description="风险说明")


class ReviewUnit(BaseModel):
    """基于上一轮OA重组后的评述单元"""
    unit_id: str = Field(..., description="评述单元唯一标识")
    unit_type: str = Field(
        "reused_oa",
        description="单元类型: reused_oa/split_from_group/merged_into_independent/supplemented_new/evidence_restructured",
    )
    source_paragraph_ids: List[str] = Field(default_factory=list, description="来源OA段落编号")
    display_claim_ids: List[str] = Field(default_factory=list, description="当前展示的权利要求编号列表")
    anchor_claim_id: str = Field("", description="排序锚点权利要求编号")
    title: str = Field("", description="展示标题")
    review_before_text: str = Field("", description="上一轮评述基线文本")
    review_text: str = Field("", description="最终生成的正式评述文本")
    claim_snapshots: List[Dict[str, Any]] = Field(default_factory=list, description="关联权利要求快照")
    source_summary: Dict[str, Any] = Field(default_factory=dict, description="调试用来源摘要")


class ErrorInfo(BaseModel):
    """错误信息"""
    node_name: str = Field(..., description="错误发生的节点名称")
    error_message: str = Field(..., description="错误信息")
    error_type: str = Field("general", description="错误类型")


class PreparedOriginalPatent(BaseModel):
    """整理后的原申请专利数据"""
    application_number: str = Field("", description="原申请号")
    data: Dict[str, Any] = Field(default_factory=dict, description="原申请专利结构化数据")


class PreparedComparisonDocument(BaseModel):
    """整理后的对比文件数据"""
    document_id: str = Field("", description="对比文件编号")
    document_number: str = Field("", description="对比文件号或名称")
    is_patent: bool = Field(False, description="是否为专利文献")
    publication_date: Optional[str] = Field(None, description="公开日期或申请日")
    data: Union[Dict[str, Any], str] = Field(default_factory=dict, description="专利为结构化数据，非专为markdown内容")


class PreparedOfficeActionParagraph(BaseModel):
    """整理后的审查意见段落"""
    paragraph_id: str = Field("", description="段落编号")
    claim_ids: List[str] = Field(default_factory=list, description="关联权利要求编号列表")
    cited_doc_ids: List[str] = Field(default_factory=list, description="明确引用的对比文件编号")
    content: str = Field("", description="段落内容")


class PreparedOfficeAction(BaseModel):
    """整理后的审查意见通知书数据"""
    application_number: str = Field("", description="原申请号")
    current_notice_round: int = Field(0, description="当前上传通知书轮次")
    paragraphs: List[PreparedOfficeActionParagraph] = Field(default_factory=list, description="段落列表")


class PreparedTextDocument(BaseModel):
    """整理后的文本类文档数据"""
    content: str = Field("", description="文档内容")


class PreparedMaterials(BaseModel):
    """整理后的关键材料结构"""
    original_patent: PreparedOriginalPatent = Field(default_factory=PreparedOriginalPatent)
    comparison_documents: List[PreparedComparisonDocument] = Field(default_factory=list)
    office_action: PreparedOfficeAction = Field(default_factory=PreparedOfficeAction)
    response: PreparedTextDocument = Field(default_factory=PreparedTextDocument)
    claims_previous: PreparedTextDocument = Field(default_factory=PreparedTextDocument)
    claims_current: PreparedTextDocument = Field(default_factory=PreparedTextDocument)
    local_retrieval: Dict[str, Any] = Field(
        default_factory=dict,
        description="任务级本地检索元信息（索引路径、版本、参数等）",
    )


class WorkflowState(BaseModel):
    """LangGraph工作流状态 - 扁平化设计"""

    # 输入文件信息
    input_files: Annotated[List[InputFile], operator.add] = Field(default_factory=list, description="输入文件列表")

    # 解析后的文件信息
    parsed_files: Annotated[List[ParsedFile], operator.add] = Field(default_factory=list, description="解析后的文件列表")
    office_action: Optional[Dict[str, Any]] = Field(None, description="审查意见通知书结构化数据")
    search_results: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list, description="专利搜索结果")
    
    # 处理后的关键数据
    prepared_materials: Optional[PreparedMaterials] = Field(None, description="整理后的关键材料结构")

    # 争辩焦点和统一核查结果 - 使用 operator.add 合并列表
    disputes: Annotated[List[Dispute], operator.add] = Field(default_factory=list, description="争辩焦点列表")
    evidence_assessments: Annotated[List[EvidenceAssessment], operator.add] = Field(default_factory=list, description="核查结果（事实核查与公知常识核查统一结构）")
    claims_previous_structured: Annotated[List[StructuredClaim], operator.add] = Field(default_factory=list, description="上一版权利要求结构化列表")
    claims_current_structured: Annotated[List[StructuredClaim], operator.add] = Field(default_factory=list, description="当前最新权利要求结构化列表")
    claims_old_structured: Annotated[List[StructuredClaim], operator.add] = Field(default_factory=list, description="当前OA审查所针对的权利要求结构化列表")
    claims_effective_structured: Annotated[List[StructuredClaim], operator.add] = Field(default_factory=list, description="当前生效权利要求结构化列表")
    has_claim_amendment: bool = Field(False, description="是否存在权利要求修改")
    added_features: Annotated[List[AddedFeature], operator.add] = Field(default_factory=list, description="新增特征列表")
    support_findings: Annotated[List[SupportFinding], operator.add] = Field(default_factory=list, description="新增特征支持依据核查结果")
    added_matter_risk: bool = Field(False, description="是否存在修改超范围风险")
    reuse_oa_tasks: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list, description="可复用历史审查意见的任务列表")
    topup_tasks: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list, description="需补充检索比对的任务列表")
    early_rejection_reason: str = Field("", description="可提前驳回的原因（如修改超范围）")
    drafted_rejection_reasons: Dict[str, str] = Field(
        default_factory=dict,
        description="统一润色后的正式驳回正文，按 dispute_id 索引",
    )
    review_units: Annotated[List[ReviewUnit], operator.add] = Field(
        default_factory=list,
        description="基于上一轮OA重组后的评述单元",
    )

    # 最终报告
    final_report: Optional[Dict[str, Any]] = Field(None, description="最终JSON报告")
    final_report_artifacts: Optional[Dict[str, str]] = Field(None, description="最终渲染产物路径（md/pdf）")

    # 错误信息
    errors: Annotated[List[ErrorInfo], operator.add] = Field(default_factory=list, description="错误信息列表")

    # 进度信息
    current_node: str = Field("start", description="当前处理节点")
    progress: float = Field(0.0, description="处理进度 0-100")
    status: str = Field("pending", description="工作流状态: pending, running, completed, failed")

    # 输出路径
    output_dir: str = Field("", description="输出目录")
    task_id: str = Field("", description="任务ID")


class WorkflowConfig(BaseModel):
    """工作流配置"""
    cache_dir: str = Field(".cache", description="缓存目录")
    timeout: int = Field(300, description="API调用超时时间(秒)")
    max_retries: int = Field(3, description="API调用最大重试次数")
    pdf_parser: str = Field("local", description="PDF解析器: local 或 online")
    enable_checkpoint: bool = Field(False, description="是否启用 LangGraph checkpoint")
    checkpoint_ns: str = Field("ai_reply", description="checkpoint 命名空间")
    checkpointer: Any = Field(default=None, description="自定义 checkpoint saver")
