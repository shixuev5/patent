<template>
  <div class="mt-4">
    <article class="wechat-card">
      <div class="wechat-header">
        <div>
          <h2 class="text-base font-semibold text-slate-900">微信 IM 渠道</h2>
          <p class="mt-1 text-xs text-slate-500">登录你自己的微信账号后，可直接在微信发起任务并接收结果回推。</p>
        </div>
        <button
          v-if="bindingStatus !== 'bound'"
          type="button"
          class="wechat-primary-btn"
          :disabled="startingLoginSession"
          @click="emit('startLoginSession')"
        >
          {{ loginButtonLabel }}
        </button>
      </div>

      <div v-if="errorMessage" class="wechat-error">
        {{ errorMessage }}
      </div>

      <section v-if="bindingStatus === 'bound' && binding" class="wechat-panel">
        <div class="wechat-panel-header">
          <div>
            <p class="wechat-badge is-bound">已登录</p>
            <h3 class="wechat-name">{{ binding.wechatDisplayName || '微信账号' }}</h3>
            <p class="wechat-meta">账号标识 {{ binding.wechatUserIdMasked || '-' }}<span v-if="binding.boundAt"> · 登录于 {{ formatTime(binding.boundAt) }}</span></p>
          </div>
          <button
            type="button"
            class="wechat-secondary-btn"
            :disabled="disconnecting"
            @click="emit('disconnect')"
          >
            {{ disconnecting ? '解绑中...' : '解绑' }}
          </button>
        </div>

        <div class="wechat-grid">
          <label class="wechat-toggle-row">
            <span>
              <span class="wechat-toggle-title">任务完成回推</span>
              <span class="wechat-toggle-help">任务完成后自动推送到微信。</span>
            </span>
            <input
              :checked="pushTaskCompleted"
              type="checkbox"
              class="wechat-checkbox"
              @change="onToggle('pushTaskCompleted', $event)"
            >
          </label>

          <label class="wechat-toggle-row">
            <span>
              <span class="wechat-toggle-title">任务失败回推</span>
              <span class="wechat-toggle-help">失败时推送错误摘要，便于及时处理。</span>
            </span>
            <input
              :checked="pushTaskFailed"
              type="checkbox"
              class="wechat-checkbox"
              @change="onToggle('pushTaskFailed', $event)"
            >
          </label>

          <label class="wechat-toggle-row">
            <span>
              <span class="wechat-toggle-title">检索待确认提醒</span>
              <span class="wechat-toggle-help">检索需要补问或确认时主动提醒。</span>
            </span>
            <input
              :checked="pushAiSearchPendingAction"
              type="checkbox"
              class="wechat-checkbox"
              @change="onToggle('pushAiSearchPendingAction', $event)"
            >
          </label>
        </div>

        <div class="wechat-actions">
          <button
            type="button"
            class="wechat-primary-btn"
            :disabled="savingSettings"
            @click="emit('saveSettings')"
          >
            {{ savingSettings ? '保存中...' : '保存推送设置' }}
          </button>
        </div>
      </section>

      <section v-else-if="loginSession" class="wechat-panel">
        <div class="wechat-bind-grid">
          <div class="wechat-qr-stage">
            <template v-if="showsQr">
              <div class="wechat-qr-box" v-html="loginSession.qrSvg" />
            </template>
            <template v-else>
              <div class="wechat-qr-box wechat-qr-box-empty">
                <p class="wechat-copy-text">正在准备微信登录二维码</p>
                <p class="wechat-step-helper">{{ statusHelperText }}</p>
              </div>
            </template>
            <div class="wechat-qr-meta">
              <p v-if="expiresText" class="wechat-qr-expire">{{ expiresText }}</p>
            </div>
          </div>

          <div class="wechat-bind-copy">
            <div class="wechat-steps">
              <div
                v-for="step in loginSteps"
                :key="step.title"
                class="wechat-step-card"
              >
                <div class="wechat-step-index">{{ step.index }}</div>
                <div class="wechat-step-content">
                  <p class="wechat-step-title">{{ step.title }}</p>
                  <p class="wechat-copy-text">{{ step.description }}</p>
                  <p v-if="step.helper" class="wechat-step-helper">{{ step.helper }}</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="wechat-phone-preview">
          <div class="wechat-phone-header">
            <span class="wechat-phone-dot" />
            <span>登录完成后</span>
          </div>
          <div class="wechat-phone-body">
            <div class="wechat-bubble is-system wechat-bubble-multiline">{{ postLoginWelcomeText }}</div>
            <div class="wechat-bubble is-user">帮我检索固态电池隔膜相关专利</div>
            <div class="wechat-bubble is-system">收到，我会先整理检索计划。</div>
          </div>
        </div>
        <p v-if="loginSession.status === 'expired'" class="wechat-inline-tip">二维码已过期，请重新生成登录二维码。</p>
        <p v-else-if="loginSession.status === 'failed'" class="wechat-inline-tip">微信登录失败：{{ loginSession.errorMessage || '请稍后重试。' }}</p>
        <p v-if="loginSession.errorMessage" class="wechat-error-inline">{{ loginSession.errorMessage }}</p>
      </section>

      <section v-else class="wechat-panel wechat-empty">
        <p class="wechat-copy-title">尚未绑定微信</p>
        <p class="wechat-copy-text">点击上方按钮登录你自己的微信账号。当前仅支持 1 个网页账号绑定 1 个微信账号。</p>
      </section>

      <section class="wechat-panel">
        <div class="wechat-usage-header">
          <div>
            <p class="wechat-copy-title">微信里可以直接这样用</p>
            <p class="wechat-copy-text">直接发自然语言需求即可，系统会按意图进入检索、分析、审查或答复流程。</p>
          </div>
        </div>

        <div class="wechat-usage-grid">
          <article
            v-for="scene in usageScenes"
            :key="scene.title"
            class="wechat-scene-card"
          >
            <p class="wechat-scene-title">{{ scene.title }}</p>
            <div class="wechat-scene-chat">
              <div class="wechat-bubble is-user">{{ scene.user }}</div>
              <div class="wechat-bubble is-system">{{ scene.reply }}</div>
            </div>
          </article>
        </div>
      </section>
    </article>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AccountWeChatBinding, AccountWeChatLoginSession } from '~/types/account'

const props = defineProps<{
  bindingStatus: 'unbound' | 'logging_in' | 'bound'
  binding: AccountWeChatBinding | null
  loginSession: AccountWeChatLoginSession | null
  pushTaskCompleted: boolean
  pushTaskFailed: boolean
  pushAiSearchPendingAction: boolean
  savingSettings: boolean
  startingLoginSession: boolean
  disconnecting: boolean
  errorMessage: string
}>()

const emit = defineEmits<{
  'update:pushTaskCompleted': [value: boolean]
  'update:pushTaskFailed': [value: boolean]
  'update:pushAiSearchPendingAction': [value: boolean]
  saveSettings: []
  startLoginSession: []
  disconnect: []
}>()

const expiresText = computed(() => {
  const text = String(props.loginSession?.expiresAt || '').trim()
  if (!text) return ''
  const expiresAt = new Date(text)
  if (Number.isNaN(expiresAt.getTime())) return ''
  const remainingMs = expiresAt.getTime() - Date.now()
  if (remainingMs <= 0) return '已过期'
  const remainingMinutes = Math.max(1, Math.ceil(remainingMs / 60000))
  return `${remainingMinutes} 分钟后过期`
})

const loginButtonLabel = computed(() => {
  if (props.startingLoginSession) return '生成中...'
  return props.loginSession ? '刷新登录二维码' : '登录我的微信账号'
})

const showsQr = computed(() => !!props.loginSession?.qrSvg)

const statusHelperText = computed(() => {
  const status = String(props.loginSession?.status || '').trim()
  if (status === 'pending') return '正在等待网关生成二维码。'
  if (status === 'scanned') return '二维码已扫描，请在微信中确认登录。'
  if (status === 'expired') return '二维码已过期，请重新生成。'
  if (status === 'failed') return props.loginSession?.errorMessage || '登录过程中出现错误。'
  return '生成后请使用微信扫码，并在手机上确认登录。'
})

const loginSteps = computed(() => {
  return [
    {
      index: '1',
      title: '扫码登录微信账号',
      description: '使用微信扫一扫左侧二维码，并在手机上确认登录。',
      helper: statusHelperText.value,
    },
    {
      index: '2',
      title: '自动完成绑定',
      description: '登录成功后，网页账号会自动绑定到当前微信账号，无需再发送绑定码。',
      helper: '',
    },
    {
      index: '3',
      title: '发送第一条任务消息',
      description: '绑定成功后，直接发任务，例如“帮我检索固态电池隔膜相关专利”。',
      helper: '',
    },
  ]
})

const usageScenes = [
  {
    title: '自然语言检索',
    user: '帮我检索固态电池隔膜相关专利',
    reply: '我会先整理检索计划，再把结果和确认动作发回这里。',
  },
  {
    title: '专利分析',
    user: '分析专利 CN117347385A',
    reply: '收到后会立即创建分析任务，完成时把结果回推到微信。',
  },
  {
    title: '专利审查',
    user: '帮我审查这个专利',
    reply: '我会先确认材料是否齐全，再创建审查任务并把结果回推到微信。',
  },
  {
    title: '审查意见答复',
    user: '我要答复审查意见',
    reply: '我会先收集答复所需材料，再按流程推进并把结果回推到微信。',
  },
] as const

const postLoginWelcomeText = [
  '微信登录成功',
  '',
  '当前网页账号已自动绑定到这个微信账号。',
  '现在可以直接在这里发 AI 检索、专利分析、专利审查和审查意见答复需求。',
  '',
  '示例：',
  '检索：帮我检索固态电池隔膜相关专利',
  '分析：分析专利 CN117347385A',
  '审查：帮我审查这个专利',
  '答复：我要答复审查意见',
  '',
  '直接发送你的需求即可。',
].join('\n')

const formatTime = (value?: string | null) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return date.toLocaleString('zh-CN', { hour12: false })
}

const onToggle = (field: 'pushTaskCompleted' | 'pushTaskFailed' | 'pushAiSearchPendingAction', event: Event) => {
  const checked = (event.target as HTMLInputElement).checked
  if (field === 'pushTaskCompleted') emit('update:pushTaskCompleted', checked)
  else if (field === 'pushTaskFailed') emit('update:pushTaskFailed', checked)
  else emit('update:pushAiSearchPendingAction', checked)
}
</script>

<style scoped>
.wechat-card {
  border: 1px solid #e2e8f0;
  border-radius: 1.25rem;
  background:
    radial-gradient(circle at top right, rgba(34, 211, 238, 0.14), transparent 28%),
    linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  padding: 1rem;
}

.wechat-header,
.wechat-panel-header,
.wechat-actions,
.wechat-usage-header {
  display: flex;
  align-items: stretch;
  flex-direction: column;
  justify-content: space-between;
  gap: 0.75rem;
}

.wechat-panel {
  margin-top: 1rem;
  border: 1px solid #e2e8f0;
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.94);
  padding: 1rem;
}

.wechat-actions {
  margin-top: 1rem;
}

.wechat-empty {
  background: #f8fafc;
}

.wechat-badge {
  display: inline-flex;
  border-radius: 9999px;
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  padding: 0.15rem 0.55rem;
  font-size: 0.72rem;
  font-weight: 600;
  color: #1d4ed8;
}

.wechat-badge.is-bound {
  border-color: #86efac;
  background: #f0fdf4;
  color: #15803d;
}

.wechat-meta,
.wechat-copy-text,
.wechat-usage-hint {
  margin: 0.35rem 0 0;
  font-size: 0.8rem;
  color: #64748b;
}

.wechat-grid {
  display: grid;
  gap: 0.8rem;
  margin-top: 1rem;
}

.wechat-toggle-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  border: 1px solid #dbeafe;
  border-radius: 0.9rem;
  background: #f8fbff;
  padding: 0.85rem 1rem;
}

.wechat-toggle-title,
.wechat-copy-title,
.wechat-scene-title {
  display: block;
  font-size: 0.85rem;
  font-weight: 600;
  color: #0f172a;
}

.wechat-toggle-help {
  display: block;
  margin-top: 0.2rem;
  font-size: 0.74rem;
  color: #64748b;
}

.wechat-checkbox {
  flex: 0 0 auto;
  height: 1rem;
  margin-top: 0.15rem;
  width: 1rem;
}

.wechat-primary-btn,
.wechat-secondary-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2.5rem;
  border-radius: 0.9rem;
  padding: 0.45rem 1rem;
  font-size: 0.85rem;
  font-weight: 600;
  transition: all 0.18s ease;
  white-space: nowrap;
  width: 100%;
}

.wechat-primary-btn {
  border: 1px solid #0891b2;
  background: linear-gradient(135deg, #06b6d4 0%, #0891b2 100%);
  color: #ecfeff;
  box-shadow: 0 10px 24px rgba(8, 145, 178, 0.2);
}

.wechat-secondary-btn {
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #334155;
}

.wechat-primary-btn:disabled,
.wechat-secondary-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.wechat-bind-grid,
.wechat-usage-grid {
  display: grid;
  gap: 1rem;
  margin-top: 0.75rem;
}

.wechat-qr-stage {
  display: grid;
  gap: 0.6rem;
  align-content: start;
}

.wechat-qr-box {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #dbeafe;
  border-radius: 1.1rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  padding: 1rem;
}

.wechat-qr-box-empty {
  min-height: 15rem;
  flex-direction: column;
  gap: 0.55rem;
  text-align: center;
}

.wechat-qr-box :deep(svg) {
  width: min(100%, 15rem);
  height: auto;
}

.wechat-scene-card {
  border: 1px solid #dbeafe;
  border-radius: 1rem;
  background: #f8fbff;
  padding: 0.9rem;
  font-size: 0.76rem;
  line-height: 1.55;
}

.wechat-qr-meta {
  display: grid;
  gap: 0.1rem;
  justify-items: center;
  text-align: center;
}

.wechat-qr-expire {
  margin: 0;
  font-size: 0.76rem;
  color: #64748b;
}

.wechat-phone-preview {
  margin-top: 1rem;
  overflow: hidden;
  border: 1px solid #dbeafe;
  border-radius: 1.2rem;
  background: linear-gradient(180deg, #f0fdfd 0%, #e2f6f7 100%);
}

.wechat-phone-header {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.75rem 0.9rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
  font-size: 0.76rem;
  font-weight: 600;
  color: #0f172a;
}

.wechat-steps {
  display: grid;
  gap: 0.75rem;
}

.wechat-step-card {
  display: grid;
  grid-template-columns: 2rem minmax(0, 1fr);
  gap: 0.75rem;
  align-items: start;
  border: 1px solid #dbeafe;
  border-radius: 1rem;
  background: #f8fbff;
  padding: 0.85rem;
}

.wechat-step-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 9999px;
  background: linear-gradient(135deg, #0f766e 0%, #14b8a6 100%);
  font-size: 0.85rem;
  font-weight: 700;
  color: #f0fdfa;
}

.wechat-step-title {
  font-size: 0.82rem;
  font-weight: 700;
  color: #0f172a;
}

.wechat-step-code {
  display: inline-flex;
  align-items: center;
  min-height: 2.75rem;
  max-width: 100%;
  margin-top: 0.55rem;
  border: 1px dashed #7dd3fc;
  border-radius: 0.9rem;
  background: #f8fdff;
  padding: 0.35rem 0.9rem;
  font-size: 1.15rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #0f172a;
  overflow-wrap: anywhere;
}

.wechat-step-helper {
  margin-top: 0.45rem;
  font-size: 0.76rem;
  line-height: 1.55;
  color: #64748b;
}

.wechat-phone-dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 9999px;
  background: #10b981;
  box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.12);
}

.wechat-phone-body,
.wechat-scene-chat {
  display: grid;
  gap: 0.6rem;
  padding: 0.9rem;
}

.wechat-bubble {
  max-width: 100%;
  border-radius: 1rem;
  padding: 0.75rem 0.85rem;
  font-size: 0.78rem;
  line-height: 1.5;
}

.wechat-bubble-multiline {
  white-space: pre-line;
}

.wechat-bubble.is-user {
  justify-self: end;
  border-bottom-right-radius: 0.35rem;
  background: linear-gradient(135deg, #0f766e 0%, #14b8a6 100%);
  color: #f0fdfa;
}

.wechat-bubble.is-system {
  justify-self: start;
  border-bottom-left-radius: 0.35rem;
  background: #ffffff;
  color: #334155;
  box-shadow: 0 10px 24px rgba(148, 163, 184, 0.12);
}

.wechat-error,
.wechat-error-inline {
  margin-top: 0.8rem;
  border-radius: 0.9rem;
  background: #fff1f2;
  padding: 0.75rem 0.9rem;
  font-size: 0.8rem;
  color: #e11d48;
}

.wechat-error-inline {
  border: 1px solid #fecdd3;
}

.wechat-inline-tip {
  margin-top: 0.8rem;
  font-size: 0.8rem;
  color: #0f766e;
}

@media (min-width: 768px) {
  .wechat-header,
  .wechat-panel-header,
  .wechat-usage-header {
    align-items: flex-start;
    flex-direction: row;
  }

  .wechat-actions {
    flex-direction: row;
    justify-content: flex-end;
  }

  .wechat-primary-btn,
  .wechat-secondary-btn {
    width: auto;
  }

  .wechat-toggle-row {
    align-items: center;
  }

  .wechat-bubble {
    max-width: 85%;
  }

  .wechat-bind-grid {
    grid-template-columns: 16rem minmax(0, 1fr);
    align-items: start;
  }

  .wechat-usage-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 1024px) {
  .wechat-bind-grid {
    grid-template-columns: minmax(16rem, 19rem) minmax(0, 1fr);
  }
}
</style>
