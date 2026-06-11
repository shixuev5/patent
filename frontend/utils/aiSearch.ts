export const AI_SEARCH_EXECUTION_PHASES = ['running']

export const AI_SEARCH_PHASE_LABELS: Record<string, string> = {
  idle: '等待指令',
  running: '检索中',
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
