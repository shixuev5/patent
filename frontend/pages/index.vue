<template>
  <div class="space-y-4">
    <section id="assistant" class="rounded-3xl border border-slate-200 bg-white/94 p-5 shadow-sm shadow-slate-200 sm:p-6">
      <div class="grid gap-5 lg:grid-cols-[1.08fr_0.92fr] lg:items-center">
        <div>
          <div class="mb-3 flex flex-wrap gap-2">
            <span class="chip">提高审查效率</span>
            <span class="chip">提升结论准确性</span>
            <span class="chip">减少重复工作</span>
          </div>

          <h1 class="font-display text-[2rem] font-semibold leading-tight text-slate-900 sm:text-[2.25rem]">
            审查员 AI 助手
            <span class="block text-cyan-700">围绕审查员协同四个核心能力</span>
          </h1>

          <p class="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
            产品按审查员工作链路设计，让审查员从信息拉取转向方向与结果判断。
            当前工作链重点由 AI 分析与 AI 研判驱动，并通过 AI 检索与 AI 撰写形成完整协同闭环。
          </p>

          <div class="mt-5 flex flex-wrap gap-3">
            <NuxtLink to="/tasks" class="cta-btn">立即创建 AI 任务</NuxtLink>
            <NuxtLink to="/changelog" class="ghost-btn">查看版本更新</NuxtLink>
            <span class="inline-flex items-center px-0 py-1.5 text-xs text-slate-500">
              已分析 {{ analyzedPatentCountText }} 个任务
            </span>
          </div>
        </div>

        <aside class="rounded-2xl border border-slate-200 bg-slate-50/90 p-4">
          <p class="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">Examiner Workbench</p>

          <div class="orbit-board mt-3">
            <div class="orbit-ring orbit-ring-a" />
            <div class="orbit-ring orbit-ring-b" />

            <div class="orbit-node orbit-top orbit-strong">AI 分析</div>
            <div class="orbit-node orbit-left orbit-soft">AI 检索</div>
            <div class="orbit-node orbit-right orbit-soft">AI 撰写</div>
            <div class="orbit-node orbit-bottom orbit-strong">AI 研判</div>

            <div class="center-avatar">
              <div class="avatar-icon" aria-hidden="true">
                <span class="avatar-head" />
                <span class="avatar-body" />
              </div>
              <span>审查员</span>
            </div>
          </div>

        </aside>
      </div>
    </section>

    <section class="rounded-3xl border border-slate-200 bg-white/94 p-5 shadow-sm shadow-slate-200 sm:p-6">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-xl font-semibold text-slate-900">从 Pull 到 Push 的价值重构</h2>
        <span class="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-700">Value Shift</span>
      </div>

      <div class="grid gap-3 lg:grid-cols-[1fr_auto_1fr] lg:items-stretch">
        <article class="flow-card rounded-2xl p-4">
          <p class="flow-tag">传统 Pull</p>
          <ul class="mt-2 space-y-1.5 text-sm text-slate-600">
            <li>审查员主动翻阅并提炼大量材料</li>
            <li>多轮检索与证据对比依赖人工串联</li>
            <li>通知书结构和表述大量重复组织</li>
          </ul>
        </article>

        <div class="hidden items-center justify-center lg:flex">
          <span class="arrow-badge">→</span>
        </div>

        <article class="flow-card flow-card-active rounded-2xl p-4">
          <p class="flow-tag">AI Push + 人工判断</p>
          <ul class="mt-2 space-y-1.5 text-sm text-slate-700">
            <li>AI 推送结构化结论和候选证据</li>
            <li>AI 组织通知书草案与理由链条</li>
            <li>审查员聚焦方向判断与结果裁决</li>
          </ul>
        </article>
      </div>
    </section>

    <section class="rounded-3xl border border-slate-200 bg-white/94 p-5 shadow-sm shadow-slate-200 sm:p-6">
      <div class="mb-3 flex items-center justify-between">
        <h2 class="text-xl font-semibold text-slate-900">审查员四阶段工作链路</h2>
        <span class="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-700">Stage Support</span>
      </div>

      <div class="grid gap-3 lg:grid-cols-4 lg:gap-4">
        <div
          v-for="(phase, index) in phaseCards"
          :key="phase.stage"
          class="phase-col relative"
        >
          <article class="stage-card card-lite card-compact rounded-2xl p-4" :class="phase.soft ? 'card-soft' : ''">
            <p class="text-xs font-semibold text-cyan-700">{{ phase.stage }}</p>
            <h3 class="mt-1.5 text-lg font-semibold text-slate-900">{{ phase.title }}</h3>
            <p class="mt-1 line-clamp-2 text-sm leading-6 text-slate-600">{{ phase.stageDescription }}</p>
          </article>

          <div class="support-link" />

          <article class="capability-card card-compact rounded-2xl p-4" :class="phase.soft ? 'capability-soft' : 'capability-strong'">
            <p class="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">核心能力</p>
            <h4 class="mt-1.5 text-lg font-semibold text-slate-900">{{ phase.capability }}</h4>
            <p class="mt-1 line-clamp-2 text-sm leading-6 text-slate-600">{{ phase.capabilityDescription }}</p>
          </article>

          <div v-if="index < phaseCards.length - 1" class="phase-arrow hidden lg:flex" aria-hidden="true">→</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

const config = useRuntimeConfig()

const analyzedPatentCount = ref<number | null>(null)

const analyzedPatentCountText = computed(() => {
  if (analyzedPatentCount.value === null) return '--'
  return new Intl.NumberFormat('zh-CN').format(analyzedPatentCount.value)
})

const phaseCards = [
  {
    stage: '阶段1',
    title: '专利理解',
    stageDescription: '快速提炼技术要点与争议焦点，减少反复通读。',
    capability: 'AI 分析',
    capabilityDescription: '结构化理解专利内容，提升审查效率。',
    soft: false,
  },
  {
    stage: '阶段2',
    title: '专利检索',
    stageDescription: '围绕焦点组织证据线索，提高检索准确性。',
    capability: 'AI 检索',
    capabilityDescription: '组织候选证据路径，减少无效检索。',
    soft: true,
  },
  {
    stage: '阶段3',
    title: '通知书撰写',
    stageDescription: '统一条理与依据结构，减少重复撰写。',
    capability: 'AI 撰写',
    capabilityDescription: '生成草案框架，降低格式与措辞重复劳动。',
    soft: true,
  },
  {
    stage: '阶段4',
    title: '审查裁决',
    stageDescription: '聚焦方向与结果判断，提升结论一致性。',
    capability: 'AI 研判',
    capabilityDescription: '归纳结论与依据，支撑高价值裁决。',
    soft: false,
  },
]

const fetchHealthStats = async () => {
  try {
    const response = await fetch(`${config.public.apiBaseUrl}/api/health`)
    if (!response.ok) throw new Error(`status: ${response.status}`)
    const data = await response.json()
    analyzedPatentCount.value = Number(data?.statistics?.completed_patents ?? data?.statistics?.by_status?.completed ?? 0)
  } catch (error) {
    if (analyzedPatentCount.value === null) analyzedPatentCount.value = 0
    console.error('Failed to fetch health stats:', error)
  }
}

onMounted(() => {
  fetchHealthStats()
})
</script>

<style scoped>
.font-display {
  font-family: 'Orbitron', 'Noto Sans SC', sans-serif;
}

.chip {
  border-radius: 9999px;
  border: 1px solid #bae6fd;
  background: #f0f9ff;
  padding: 0.25rem 0.72rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: #0c4a6e;
}

.cta-btn,
.ghost-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  padding: 0.58rem 0.95rem;
  font-size: 0.84rem;
  font-weight: 600;
}

.cta-btn {
  background: #0891b2;
  color: #fff;
}

.cta-btn:hover {
  background: #0e7490;
}

.ghost-btn {
  border: 1px solid #d1d5db;
  color: #334155;
  background: #fff;
}

.ghost-btn:hover {
  background: #f8fafc;
}

.orbit-board {
  position: relative;
  height: 220px;
}

.orbit-ring {
  position: absolute;
  left: 50%;
  top: 50%;
  border-radius: 9999px;
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.orbit-ring-a {
  width: 184px;
  height: 184px;
  border: 1px dashed rgba(14, 165, 233, 0.45);
  animation: spin 16s linear infinite;
}

.orbit-ring-b {
  width: 146px;
  height: 146px;
  border: 1px dashed rgba(56, 189, 248, 0.35);
  animation: spin-reverse 12s linear infinite;
}

.orbit-node {
  position: absolute;
  min-width: 78px;
  text-align: center;
  border-radius: 0.85rem;
  border: 1px solid #bae6fd;
  padding: 0.42rem 0.56rem;
  font-size: 0.82rem;
  font-weight: 700;
  backdrop-filter: blur(3px);
}

.orbit-strong {
  background: #dff6ff;
  color: #0e7490;
}

.orbit-soft {
  background: #f8fafc;
  border-color: #e2e8f0;
  color: #94a3b8;
}

.orbit-top {
  left: 50%;
  top: 14px;
  transform: translateX(-50%);
}

.orbit-left {
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
}

.orbit-right {
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
}

.orbit-bottom {
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
}

.center-avatar {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 96px;
  height: 96px;
  border-radius: 9999px;
  border: 2px solid #22d3ee;
  background: radial-gradient(circle at 30% 20%, #f0f9ff, #dff6ff);
  box-shadow: 0 0 0 6px rgba(34, 211, 238, 0.08);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.2rem;
  color: #155e75;
  font-size: 0.78rem;
  font-weight: 800;
}

.avatar-icon {
  position: relative;
  width: 16px;
  height: 16px;
}

.avatar-head {
  position: absolute;
  left: 50%;
  top: 0;
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  transform: translateX(-50%);
  background: #0891b2;
}

.avatar-body {
  position: absolute;
  left: 50%;
  bottom: 0;
  width: 11px;
  height: 7px;
  border-radius: 8px 8px 4px 4px;
  transform: translateX(-50%);
  background: #0891b2;
}

.flow-card {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
}

.flow-card-active {
  border-color: #67e8f9;
  background: #ecfeff;
}

.flow-tag {
  font-size: 0.82rem;
  font-weight: 700;
  color: #0f172a;
}

.arrow-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 9999px;
  background: #0891b2;
  color: #fff;
  font-weight: 700;
}

.card-lite {
  border: 1px solid #e2e8f0;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 8px 20px rgba(148, 163, 184, 0.14);
}

.card-compact {
  min-height: 132px;
}

.card-soft {
  background: rgba(248, 250, 252, 0.9);
}

.phase-col {
  position: relative;
}

.support-link {
  width: 0;
  height: 14px;
  margin: 0.25rem auto;
  border-left: 1px dashed #7dd3fc;
}

.capability-card {
  border: 1px dashed #7dd3fc;
  box-shadow: 0 8px 16px rgba(125, 211, 252, 0.18);
}

.capability-strong {
  background: #f0f9ff;
}

.capability-soft {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.phase-arrow {
  position: absolute;
  top: 52px;
  right: -12px;
  z-index: 5;
  width: 1.45rem;
  height: 1.45rem;
  border-radius: 9999px;
  background: #0891b2;
  color: white;
  align-items: center;
  justify-content: center;
  font-size: 0.78rem;
  font-weight: 700;
}

@keyframes spin {
  from { transform: translate(-50%, -50%) rotate(0deg); }
  to { transform: translate(-50%, -50%) rotate(360deg); }
}

@keyframes spin-reverse {
  from { transform: translate(-50%, -50%) rotate(360deg); }
  to { transform: translate(-50%, -50%) rotate(0deg); }
}

@media (prefers-reduced-motion: reduce) {
  .orbit-ring-a,
  .orbit-ring-b {
    animation: none;
  }
}
</style>
