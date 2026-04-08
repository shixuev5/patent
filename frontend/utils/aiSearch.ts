export const AI_SEARCH_EXECUTION_PHASES = [
  'execute_search',
  'coarse_screen',
  'close_read',
  'feature_comparison',
]

export const AI_SEARCH_PHASE_LABELS: Record<string, string> = {
  collecting_requirements: '整理需求',
  awaiting_user_answer: '等待回答',
  drafting_plan: '起草计划',
  awaiting_plan_confirmation: '待确认',
  execute_search: '执行检索',
  coarse_screen: '粗筛候选文献',
  close_read: '精读并提取证据',
  feature_comparison: '特征对比分析',
  awaiting_human_decision: '等待人工决策',
  completed: '已完成',
  failed: '失败',
  cancelled: '已终止',
}

export const normalizeAiSearchPhase = (phase: string): string => {
  return String(phase || '').trim()
}

export const isAiSearchExecutionPhase = (phase: string): boolean => {
  return AI_SEARCH_EXECUTION_PHASES.includes(normalizeAiSearchPhase(phase))
}

export const aiSearchPhaseLabel = (phase: string): string => {
  const normalized = normalizeAiSearchPhase(phase)
  return AI_SEARCH_PHASE_LABELS[normalized] || normalized || '未知阶段'
}
