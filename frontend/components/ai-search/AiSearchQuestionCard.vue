<template>
  <section class="space-y-3">
    <div class="rounded-2xl border border-amber-200 bg-amber-50/90 px-4 py-3">
      <p class="text-sm font-semibold text-amber-900">待回答问题</p>
      <p class="mt-1 whitespace-pre-wrap text-sm leading-6 text-amber-900">{{ prompt }}</p>
      <p v-if="reason" class="mt-2 text-xs text-amber-700">原因：{{ reason }}</p>
      <p v-if="answerShape" class="mt-1 text-xs text-amber-700">期望答案：{{ answerShape }}</p>
    </div>
    <textarea
      :value="modelValue"
      rows="4"
      class="w-full rounded-2xl border border-amber-200 bg-white px-4 py-3 text-sm text-slate-900 outline-none transition focus:border-amber-400 focus:ring-2 focus:ring-amber-100"
      placeholder="请输入补充信息。"
      @input="onInput"
    />
    <div class="flex justify-end">
      <button
        type="button"
        class="rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        :disabled="disabled"
        @click="$emit('submit')"
      >
        提交回答
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
const props = defineProps<{
  prompt: string
  reason?: string
  answerShape?: string
  modelValue: string
  disabled: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
}>()

const onInput = (event: Event) => {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}
</script>
