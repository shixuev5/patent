<template>
  <div class="mt-4">
    <section class="notification-panel">
      <div class="notification-toolbar">
        <div class="min-w-0">
          <h2 class="notification-title">邮件通知</h2>
          <p class="notification-summary">开启后，工作邮箱和个人邮箱中已填写的地址都会收到任务完成或失败通知。</p>
        </div>

        <div class="notification-toolbar-actions">
          <label class="notification-toggle-inline">
            <span class="notification-toggle-text">{{ notificationEmailEnabled ? '已开启' : '已关闭' }}</span>
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

          <button
            type="button"
            class="notification-save-btn"
            :disabled="savingNotificationSettings || notificationFormInvalid"
            @click="emit('save')"
          >
            {{ savingNotificationSettings ? '保存中...' : '保存设置' }}
          </button>
        </div>
      </div>

      <div class="notification-form-grid">
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
        </label>
      </div>

      <p class="notification-hint">
        关闭后不会发送邮件，但会保留已填写的邮箱。开启通知时，两个邮箱至少填写一个；若填写相同地址，实际发送会自动去重。
      </p>

      <p v-if="notificationSettingsErrorMessage" class="notification-settings-error">
        {{ notificationSettingsErrorMessage }}
      </p>
    </section>
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
.notification-panel {
  border: 1px solid #e2e8f0;
  border-radius: 1rem;
  background: #ffffff;
  padding: 1rem;
}

.notification-toolbar {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 0.9rem;
}

.notification-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 700;
  color: #0f172a;
}

.notification-summary {
  margin: 0.35rem 0 0;
  font-size: 0.8rem;
  color: #64748b;
}

.notification-toolbar-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 1rem;
}

.notification-toggle-inline {
  display: inline-flex;
  align-items: center;
  gap: 0.7rem;
}

.notification-toggle-text {
  font-size: 0.82rem;
  font-weight: 600;
  color: #334155;
}

.notification-form-grid {
  display: grid;
  gap: 0.9rem;
  margin-top: 1rem;
}

.notification-field-label {
  font-size: 0.85rem;
  font-weight: 600;
  color: #0f172a;
}

.notification-field {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
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
  border-radius: 0.8rem;
  background: #ffffff;
  padding: 0.65rem 0.8rem;
  font-size: 0.88rem;
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

.notification-hint {
  margin: 0.75rem 0 0;
  font-size: 0.76rem;
  line-height: 1.5;
  color: #64748b;
}

.notification-settings-error {
  margin: 0.6rem 0 0;
  font-size: 0.8rem;
  color: #e11d48;
}

.notification-save-btn {
  min-height: 2.35rem;
  border-radius: 0.8rem;
  border: 1px solid #0891b2;
  background: #06b6d4;
  padding: 0.42rem 0.9rem;
  font-size: 0.82rem;
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

@media (min-width: 768px) {
  .notification-toolbar {
    flex-direction: row;
    align-items: center;
  }

  .notification-toolbar-actions {
    justify-content: flex-end;
  }

  .notification-form-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
