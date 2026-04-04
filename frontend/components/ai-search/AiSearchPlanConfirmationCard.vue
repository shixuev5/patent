<template>
  <section class="space-y-3">
    <div class="rounded-2xl border border-cyan-200 bg-cyan-50/90 px-4 py-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="text-sm font-semibold text-cyan-900">当前计划待确认</p>
          <p class="mt-1 text-xs text-cyan-800">可以继续发送调整意见，也可以直接确认当前计划。</p>
        </div>
        <button
          type="button"
          class="rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          :disabled="confirmDisabled"
          @click="$emit('confirm')"
        >
          确认检索计划
        </button>
      </div>
    </div>
    <div class="relative">
      <textarea
        :value="modelValue"
        rows="4"
        class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 pr-32 text-sm text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-50"
        :disabled="inputDisabled"
        placeholder="继续输入对当前检索计划的调整意见"
        @input="onInput"
        @keydown.meta.enter.prevent="$emit('submit')"
        @keydown.ctrl.enter.prevent="$emit('submit')"
      />
      <button
        type="button"
        class="absolute bottom-3 right-3 rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
        :disabled="submitDisabled"
        @click="$emit('submit')"
      >
        发送消息
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
defineProps<{
  modelValue: string
  inputDisabled: boolean
  submitDisabled: boolean
  confirmDisabled: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
  confirm: []
}>()

const onInput = (event: Event) => {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}
</script>
