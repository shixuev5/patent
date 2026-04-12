<template>
  <div class="inline-flex rounded-2xl border border-slate-200 bg-slate-50 p-1">
    <button
      type="button"
      class="account-tab-btn"
      :class="activeTab === 'overview' ? 'is-active' : ''"
      @click="emit('update:activeTab', 'overview')"
    >
      概览
    </button>
    <button
      v-if="hasNotificationTab"
      type="button"
      class="account-tab-btn"
      :class="activeTab === 'notifications' ? 'is-active' : ''"
      @click="emit('update:activeTab', 'notifications')"
    >
      邮件通知
    </button>
    <button
      v-if="hasWechatTab"
      type="button"
      class="account-tab-btn"
      :class="activeTab === 'wechat' ? 'is-active' : ''"
      @click="emit('update:activeTab', 'wechat')"
    >
      微信接入
    </button>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  activeTab: 'overview' | 'notifications' | 'wechat'
  hasNotificationTab: boolean
  hasWechatTab: boolean
}>()

const emit = defineEmits<{
  'update:activeTab': [value: 'overview' | 'notifications' | 'wechat']
}>()
</script>

<style scoped>
.account-tab-btn {
  border-radius: 0.85rem;
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: #475569;
  transition: all 0.18s ease;
}

.account-tab-btn.is-active {
  background: #ffffff;
  color: #0f172a;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}
</style>
