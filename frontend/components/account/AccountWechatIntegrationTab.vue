<template>
  <div class="mt-4">
    <article class="wechat-card">
      <div class="wechat-header">
        <div>
          <h2 class="text-base font-semibold text-slate-900">微信 IM 渠道</h2>
          <p class="mt-1 text-xs text-slate-500">绑定后，即使不打开网页，也可以通过微信私聊发起检索、分析、审查和答复任务，并接收结果回推。</p>
        </div>
        <button
          v-if="bindingStatus !== 'bound'"
          type="button"
          class="wechat-primary-btn"
          :disabled="startingBindSession"
          @click="emit('startBindSession')"
        >
          {{ bindButtonLabel }}
        </button>
      </div>

      <div v-if="errorMessage" class="wechat-error">
        {{ errorMessage }}
      </div>

      <section v-if="bindingStatus === 'bound' && binding" class="wechat-panel">
        <div class="wechat-panel-header">
          <div>
            <p class="wechat-badge is-bound">已绑定</p>
            <h3 class="wechat-name">{{ binding.wechatPeerName || '微信私聊身份' }}</h3>
            <p class="wechat-meta">账号标识 {{ binding.wechatPeerIdMasked || '-' }}<span v-if="binding.boundAt"> · 绑定于 {{ formatTime(binding.boundAt) }}</span></p>
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
              <span class="wechat-toggle-help">分析、审查、答复完成后主动推送到微信。</span>
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
              <span class="wechat-toggle-help">失败时把错误摘要推送到微信，便于及时恢复处理。</span>
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
              <span class="wechat-toggle-help">当 AI 检索需要补问、确认计划或人工决策时主动提醒。</span>
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

      <section v-else-if="bindSession" class="wechat-panel">
        <div class="wechat-panel-header">
          <div>
            <p class="wechat-badge" :class="bindBadgeClass">{{ bindBadgeLabel }}</p>
            <h3 class="wechat-name">{{ bindStatusTitle }}</h3>
            <p class="wechat-meta">{{ bindStatusSummary }}<span v-if="expiresText"> · {{ expiresText }}</span></p>
          </div>
        </div>

        <div class="wechat-bind-grid">
          <div class="wechat-qr-stage">
            <div class="wechat-qr-box" v-html="bindSession.qrSvg" />
            <p class="wechat-qr-caption">{{ qrCaptionText }}</p>
          </div>

          <div class="wechat-bind-copy">
            <p class="wechat-copy-title">三步完成绑定并开始第一次对话</p>
            <p class="wechat-copy-text">{{ stepIntroText }}</p>
            <div class="wechat-steps">
              <div
                v-for="step in bindSteps"
                :key="step.title"
                class="wechat-step-card"
              >
                <div class="wechat-step-index">{{ step.index }}</div>
                <div class="wechat-step-content">
                  <p class="wechat-step-title">{{ step.title }}</p>
                  <p class="wechat-copy-text">{{ step.description }}</p>
                  <p v-if="step.code" class="wechat-step-code">{{ step.code }}</p>
                  <p v-if="step.helper" class="wechat-step-helper">{{ step.helper }}</p>
                </div>
              </div>
            </div>
            <div class="wechat-phone-preview">
              <div class="wechat-phone-header">
                <span class="wechat-phone-dot" />
                <span>首次对话示例</span>
              </div>
              <div class="wechat-phone-body">
                <div class="wechat-bubble is-system">欢迎使用专利审查助手。请发送绑定码完成网页账号绑定。</div>
                <div class="wechat-bubble is-user">{{ bindSession.bindCode }}</div>
                <div class="wechat-bubble is-system">绑定成功。现在可以直接发任务，例如“帮我检索固态电池隔膜相关专利”。</div>
                <div class="wechat-bubble is-user">帮我检索固态电池隔膜相关专利</div>
                <div class="wechat-bubble is-system">收到，我会先整理检索计划，确认后继续执行。</div>
              </div>
            </div>
            <p v-if="bindSession.gatewayStatus === 'expired'" class="wechat-inline-tip">接入微信二维码刚刚过期，网关会自动刷新，页面轮询后会显示新二维码。</p>
            <p v-else-if="bindSession.gatewayStatus === 'error'" class="wechat-inline-tip">接入微信号正在重连：{{ bindSession.gatewayErrorMessage || '请稍后重试。' }}</p>
            <p v-if="bindSession.errorMessage" class="wechat-error-inline">{{ bindSession.errorMessage }}</p>
          </div>
        </div>
      </section>

      <section v-else class="wechat-panel wechat-empty">
        <p class="wechat-copy-title">尚未绑定微信</p>
        <p class="wechat-copy-text">点击上方按钮生成绑定二维码。当前仅支持一个网页账号绑定一个微信私聊身份。</p>
      </section>

      <section class="wechat-panel">
        <div class="wechat-usage-header">
          <div>
            <p class="wechat-copy-title">微信里可以直接这样用</p>
            <p class="wechat-copy-text">自然语言优先，斜杠命令只是兜底入口。下面这些都是用户真正会发出的对话示例。</p>
          </div>
          <p v-if="fallbackCommandsText" class="wechat-usage-hint">兜底命令：{{ fallbackCommandsText }}</p>
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
import type { AccountWeChatBindSession, AccountWeChatBinding } from '~/types/account'

const props = defineProps<{
  bindingStatus: 'unbound' | 'binding' | 'bound'
  binding: AccountWeChatBinding | null
  bindSession: AccountWeChatBindSession | null
  availableCommands: string[]
  pushTaskCompleted: boolean
  pushTaskFailed: boolean
  pushAiSearchPendingAction: boolean
  savingSettings: boolean
  startingBindSession: boolean
  disconnecting: boolean
  errorMessage: string
}>()

const emit = defineEmits<{
  'update:pushTaskCompleted': [value: boolean]
  'update:pushTaskFailed': [value: boolean]
  'update:pushAiSearchPendingAction': [value: boolean]
  saveSettings: []
  startBindSession: []
  disconnect: []
}>()

const expiresText = computed(() => {
  const text = String(props.bindSession?.expiresAt || '').trim()
  if (!text) return ''
  const expiresAt = new Date(text)
  if (Number.isNaN(expiresAt.getTime())) return ''
  const remainingMs = expiresAt.getTime() - Date.now()
  if (remainingMs <= 0) return '已过期'
  const remainingMinutes = Math.max(1, Math.ceil(remainingMs / 60000))
  return `${remainingMinutes} 分钟后过期`
})

const bindButtonLabel = computed(() => {
  if (props.startingBindSession) return '生成中...'
  return props.bindSession ? '刷新绑定二维码' : '生成绑定二维码'
})

const fallbackCommandsText = computed(() => {
  return props.availableCommands
    .filter(item => item.startsWith('/'))
    .join(' · ')
})

const bindCodeHelperText = computed(() => {
  if (props.bindSession?.qrScene === 'gateway_login') {
    return '扫码进入接入微信号后，把这串绑定码发送到聊天窗口，网页账号就会和当前微信私聊身份绑定。'
  }
  return '如果当前不方便扫码，可以直接把这串绑定码发给接入微信号。'
})

const bindBadgeLabel = computed(() => {
  if (props.bindSession?.status === 'bound') return '绑定成功'
  if (props.bindSession?.gatewayStatus === 'scanned') return '已扫码'
  if (props.bindSession?.gatewayStatus === 'error') return '接入异常'
  if (props.bindSession?.gatewayStatus === 'expired') return '二维码已过期'
  return '等待扫码'
})

const bindBadgeClass = computed(() => {
  if (props.bindSession?.status === 'bound') return 'is-bound'
  if (props.bindSession?.gatewayStatus === 'scanned') return 'is-scanned'
  if (props.bindSession?.gatewayStatus === 'error') return 'is-error'
  return ''
})

const bindStatusTitle = computed(() => {
  if (props.bindSession?.status === 'bound') return '微信私聊身份已绑定'
  if (props.bindSession?.gatewayStatus === 'scanned') return '继续在微信里发送绑定码'
  return '扫码绑定微信私聊身份'
})

const bindStatusSummary = computed(() => {
  if (!props.bindSession) return ''
  if (props.bindSession.status === 'bound') return `绑定码 ${props.bindSession.bindCode}`
  if (props.bindSession.gatewayStatus === 'scanned') return `已完成扫码，下一步发送绑定码 ${props.bindSession.bindCode}`
  return `绑定码 ${props.bindSession.bindCode}`
})

const qrCaptionText = computed(() => {
  if (props.bindSession?.qrScene === 'gateway_login') {
    if (props.bindSession.gatewayStatus === 'scanned') {
      return '二维码已扫描。请回到微信里的接入会话，发送右侧绑定码完成网页账号绑定。'
    }
    return '用微信扫一扫这个二维码，进入接入微信号。'
  }
  return '当前展示的是绑定载荷二维码。如果扫码体验不稳定，可直接复制右侧绑定码到已打开的接入会话。'
})

const stepIntroText = computed(() => {
  if (props.bindSession?.qrScene === 'gateway_login') {
    if (props.bindSession.gatewayStatus === 'scanned') {
      return '你已经完成扫码，当前只差一步：把绑定码发送到微信聊天窗口，网页账号才会和这次扫码的微信私聊身份绑定。'
    }
    return '左侧二维码会把你带到接入微信号；进入聊天后发送绑定码，再发第一条任务消息即可。'
  }
  return '当前左侧仍是绑定载荷二维码，建议优先使用接入微信二维码进入聊天，再发送绑定码。'
})

const bindSteps = computed(() => {
  const usesGatewayQr = props.bindSession?.qrScene === 'gateway_login'
  return [
    {
      index: '1',
      title: usesGatewayQr ? '微信扫码进入接入号' : '进入接入微信号',
      description: usesGatewayQr
        ? '用微信扫一扫左侧二维码，手机里会打开接入微信号的入口。'
        : '请先用微信打开接入微信号的入口，再进行下面两步。',
      code: '',
      helper: '',
    },
    {
      index: '2',
      title: '发送绑定码',
      description: '把下面这串绑定码发送到聊天窗口，完成网页账号绑定。',
      code: props.bindSession?.bindCode || '',
      helper: bindCodeHelperText.value,
    },
    {
      index: '3',
      title: '发送第一条任务消息',
      description: '绑定成功后，直接发自然语言任务，例如“帮我检索固态电池隔膜相关专利”。',
      code: '',
      helper: '',
    },
  ]
})

const usageScenes = [
  {
    title: '自然语言检索',
    user: '帮我检索固态电池隔膜相关专利',
    reply: '我会先整理检索计划，再把结果、补问和确认动作直接发回这个聊天窗口。',
  },
  {
    title: '专利分析',
    user: '分析专利 CN202410009999.9',
    reply: '收到后会立即创建分析任务，完成时把摘要和结果文件回推到微信。',
  },
  {
    title: '审查 / 答复',
    user: '我要答复审查意见',
    reply: '我会按步骤收材料。过程中你可以继续上传文件，必要时随时发送 /cancel 取消。',
  },
] as const

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
  align-items: flex-start;
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

.wechat-badge.is-scanned {
  border-color: #99f6e4;
  background: #ecfeff;
  color: #0f766e;
}

.wechat-badge.is-error {
  border-color: #fecaca;
  background: #fef2f2;
  color: #b91c1c;
}

.wechat-name {
  margin: 0.45rem 0 0;
  font-size: 1rem;
  font-weight: 700;
  color: #0f172a;
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
  align-items: center;
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
  height: 1rem;
  width: 1rem;
}

.wechat-primary-btn,
.wechat-secondary-btn {
  min-height: 2.5rem;
  border-radius: 0.9rem;
  padding: 0.45rem 1rem;
  font-size: 0.85rem;
  font-weight: 600;
  transition: all 0.18s ease;
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
  margin-top: 1rem;
}

.wechat-qr-stage {
  display: grid;
  gap: 0.9rem;
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

.wechat-qr-box :deep(svg) {
  width: 15rem;
  height: 15rem;
}

.wechat-qr-caption,
.wechat-scene-card {
  font-size: 0.76rem;
  line-height: 1.55;
}

.wechat-scene-card {
  border: 1px solid #dbeafe;
  border-radius: 1rem;
  background: #f8fbff;
  padding: 0.9rem;
}

.wechat-phone-preview {
  margin-top: 0.9rem;
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
  margin-top: 0.9rem;
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
  margin-top: 0.55rem;
  border: 1px dashed #7dd3fc;
  border-radius: 0.9rem;
  background: #f8fdff;
  padding: 0.35rem 0.9rem;
  font-size: 1.15rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #0f172a;
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
  max-width: 85%;
  border-radius: 1rem;
  padding: 0.75rem 0.85rem;
  font-size: 0.78rem;
  line-height: 1.5;
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
  .wechat-bind-grid {
    grid-template-columns: 17rem minmax(0, 1fr);
    align-items: start;
  }

  .wechat-usage-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (min-width: 1024px) {
  .wechat-bind-grid {
    grid-template-columns: minmax(17rem, 21rem) minmax(0, 1fr);
  }
}
</style>
