<template>
  <div class="mt-4">
    <article class="notification-settings-card">
      <div class="notification-settings-header">
        <div>
          <h2 class="text-base font-semibold text-slate-900">任务邮件通知</h2>
          <p class="mt-1 text-xs text-slate-500">可分别配置工作邮箱和个人邮箱。开启后，已填写的地址都会收到任务完成或失败通知。</p>
        </div>
      </div>

      <div class="notification-form-grid">
        <label class="notification-toggle-row">
          <span class="notification-field-copy">
            <span class="notification-field-label">开启邮件通知</span>
            <span class="notification-field-help">关闭后不会发送邮件，但会保留已填写的邮箱。</span>
          </span>
          <span class="notification-switch">
            <input
              :checked="notificationEmailEnabled"
              type="checkbox"
              class="sr-only"
              @change="onEnabledChange"
            />
            <span class="notification-switch-track" :class="{ 'is-on': notificationEmailEnabled }" />
          </span>
        </label>

        <label class="notification-field">
          <span class="notification-field-label">工作邮箱</span>
          <input
            :value="workNotificationEmail"
            type="email"
            autocomplete="email"
            class="notification-input"
            :class="{ 'is-invalid': workNotificationEmailInvalid }"
            :disabled="!notificationEmailEnabled || savingNotificationSettings"
            placeholder="name@company.com"
            @input="onWorkEmailInput"
          />
          <span class="notification-field-help">可留空；开启通知时，工作邮箱和个人邮箱至少填写一个。</span>
        </label>

        <label class="notification-field">
          <span class="notification-field-label">个人邮箱</span>
          <input
            :value="personalNotificationEmail"
            type="email"
            autocomplete="email"
            class="notification-input"
            :class="{ 'is-invalid': personalNotificationEmailInvalid }"
            :disabled="!notificationEmailEnabled || savingNotificationSettings"
            placeholder="name@example.com"
            @input="onPersonalEmailInput"
          />
          <span class="notification-field-help">两个邮箱可以填成同一个地址，实际发送会自动去重。</span>
        </label>
      </div>

      <p v-if="notificationSettingsErrorMessage" class="notification-settings-error">
        {{ notificationSettingsErrorMessage }}
      </p>

      <div class="notification-actions">
        <button
          type="button"
          class="notification-save-btn"
          :disabled="savingNotificationSettings || notificationFormInvalid"
          @click="emit('save')"
        >
          {{ savingNotificationSettings ? '保存中...' : '保存设置' }}
        </button>
      </div>
    </article>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  notificationEmailEnabled: boolean
  workNotificationEmail: string
  personalNotificationEmail: string
  workNotificationEmailInvalid: boolean
  personalNotificationEmailInvalid: boolean
  savingNotificationSettings: boolean
  notificationFormInvalid: boolean
  notificationSettingsErrorMessage: string
}>()

const emit = defineEmits<{
  'update:notificationEmailEnabled': [value: boolean]
  'update:workNotificationEmail': [value: string]
  'update:personalNotificationEmail': [value: string]
  save: []
}>()

const onEnabledChange = (event: Event) => {
  emit('update:notificationEmailEnabled', (event.target as HTMLInputElement).checked)
}

const onWorkEmailInput = (event: Event) => {
  emit('update:workNotificationEmail', (event.target as HTMLInputElement).value)
}

const onPersonalEmailInput = (event: Event) => {
  emit('update:personalNotificationEmail', (event.target as HTMLInputElement).value)
}
</script>

<style scoped>
.notification-settings-card {
  border: 1px solid #e2e8f0;
  border-radius: 1.25rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  padding: 1rem;
}

.notification-settings-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.notification-form-grid {
  display: grid;
  gap: 0.9rem;
  margin-top: 1rem;
}

.notification-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border: 1px solid #dbeafe;
  border-radius: 1rem;
  background: #f8fbff;
  padding: 0.9rem 1rem;
}

.notification-field-copy,
.notification-field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.notification-field-label {
  font-size: 0.85rem;
  font-weight: 600;
  color: #0f172a;
}

.notification-field-help {
  font-size: 0.75rem;
  color: #64748b;
}

.notification-switch {
  display: inline-flex;
  flex-shrink: 0;
}

.notification-switch-track {
  position: relative;
  width: 2.9rem;
  height: 1.7rem;
  border-radius: 9999px;
  background: #cbd5e1;
  transition: background 0.18s ease;
}

.notification-switch-track::after {
  content: '';
  position: absolute;
  top: 0.15rem;
  left: 0.18rem;
  width: 1.4rem;
  height: 1.4rem;
  border-radius: 9999px;
  background: #ffffff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.18);
  transition: transform 0.18s ease;
}

.notification-switch-track.is-on {
  background: #06b6d4;
}

.notification-switch-track.is-on::after {
  transform: translateX(1.15rem);
}

.notification-input {
  width: 100%;
  min-height: 2.75rem;
  border: 1px solid #cbd5e1;
  border-radius: 0.9rem;
  background: #ffffff;
  padding: 0.7rem 0.85rem;
  font-size: 0.9rem;
  color: #0f172a;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.notification-input:focus {
  outline: none;
  border-color: #0891b2;
  box-shadow: 0 0 0 3px rgba(6, 182, 212, 0.14);
}

.notification-input:disabled {
  cursor: not-allowed;
  background: #f8fafc;
  color: #94a3b8;
}

.notification-input.is-invalid {
  border-color: #fb7185;
  background: #fff1f2;
}

.notification-settings-error {
  margin: 0.9rem 0 0;
  font-size: 0.8rem;
  color: #e11d48;
}

.notification-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 1rem;
}

.notification-save-btn {
  min-height: 2.5rem;
  border-radius: 0.9rem;
  border: 1px solid #0891b2;
  background: #06b6d4;
  padding: 0.45rem 1rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: #ecfeff;
  transition: all 0.18s ease;
}

.notification-save-btn:hover:not(:disabled) {
  background: #0891b2;
}

.notification-save-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
</style>
