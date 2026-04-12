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
          {{ startingBindSession ? '生成中...' : '生成绑定二维码' }}
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
            <p class="wechat-badge">{{ bindSession.status === 'bound' ? '绑定成功' : '等待扫码' }}</p>
            <h3 class="wechat-name">扫码绑定微信私聊身份</h3>
            <p class="wechat-meta">绑定码 {{ bindSession.bindCode }}<span v-if="expiresText"> · {{ expiresText }}</span></p>
          </div>
        </div>

        <div class="wechat-bind-grid">
          <div class="wechat-qr-box" v-html="bindSession.qrSvg" />
          <div class="wechat-bind-copy">
            <p class="wechat-copy-title">绑定说明</p>
            <p class="wechat-copy-text">1. 使用接入微信号扫描二维码或录入绑定码。</p>
            <p class="wechat-copy-text">2. 成功后当前账号会立即与该微信私聊身份绑定。</p>
            <p class="wechat-copy-text">3. 绑定完成后，后续任务结果会自动推送到微信。</p>
            <p v-if="bindSession.errorMessage" class="wechat-error-inline">{{ bindSession.errorMessage }}</p>
          </div>
        </div>
      </section>

      <section v-else class="wechat-panel wechat-empty">
        <p class="wechat-copy-title">尚未绑定微信</p>
        <p class="wechat-copy-text">点击上方按钮生成绑定二维码。v1 仅支持认证用户绑定一个微信私聊身份。</p>
      </section>

      <section class="wechat-panel">
        <p class="wechat-copy-title">微信中可直接使用</p>
        <p class="wechat-copy-text">支持自然语言直接发起检索、分析、审查、答复；斜杠命令只是兜底入口。</p>
        <div class="wechat-command-list">
          <code v-for="item in availableCommands" :key="item" class="wechat-command">{{ item }}</code>
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
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  padding: 1rem;
}

.wechat-header,
.wechat-panel-header,
.wechat-actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.wechat-panel {
  margin-top: 1rem;
  border: 1px solid #e2e8f0;
  border-radius: 1rem;
  background: #ffffff;
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

.wechat-name {
  margin: 0.45rem 0 0;
  font-size: 1rem;
  font-weight: 700;
  color: #0f172a;
}

.wechat-meta,
.wechat-copy-text {
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
.wechat-copy-title {
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
  background: #06b6d4;
  color: #ecfeff;
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

.wechat-bind-grid {
  display: grid;
  gap: 1rem;
  margin-top: 1rem;
}

.wechat-qr-box {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e2e8f0;
  border-radius: 1rem;
  background: #f8fafc;
  padding: 1rem;
}

.wechat-qr-box :deep(svg) {
  width: 15rem;
  height: 15rem;
}

.wechat-command-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-top: 0.75rem;
}

.wechat-command {
  border-radius: 9999px;
  background: #0f172a;
  padding: 0.35rem 0.7rem;
  font-size: 0.75rem;
  color: #f8fafc;
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

@media (min-width: 768px) {
  .wechat-bind-grid {
    grid-template-columns: 16rem minmax(0, 1fr);
    align-items: center;
  }
}
</style>
