<template>
  <div class="flex h-[70vh] w-full items-center justify-center">
    <div class="text-center">
      <div class="inline-block h-10 w-10 animate-spin rounded-full border-4 border-cyan-600 border-t-transparent" />
      <p class="mt-4 text-sm text-slate-600">登录处理中...</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from '~/stores/auth'

const authStore = useAuthStore()

onMounted(async () => {
  try {
    await authStore.handleCallback()
  } catch (error) {
    console.error('Authing callback failed:', error)
  } finally {
    await navigateTo('/account')
  }
})
</script>
