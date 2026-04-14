<template>
  <section>
    <div class="relative">
      <textarea
        :value="modelValue"
        rows="2"
        class="min-h-[5.25rem] max-h-[9.5rem] w-full resize-none overflow-y-auto rounded-2xl border border-slate-200 bg-white px-4 py-3 pb-8 pr-16 text-sm text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-50"
        :disabled="disabled"
        :placeholder="placeholder"
        @input="onInput"
        @keydown.enter.exact.prevent="onEnter"
        @keydown.meta.enter.prevent="$emit('submit')"
        @keydown.ctrl.enter.prevent="$emit('submit')"
      />
      <span class="pointer-events-none absolute bottom-3 left-4 text-[11px] text-slate-400">
        Enter 发送，Shift+Enter 换行
      </span>
      <button
        type="button"
        class="absolute bottom-3 right-3 inline-flex h-9 w-9 items-center justify-center rounded-full bg-cyan-700 text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
        aria-label="发送消息"
        :disabled="!canSubmit"
        @click="$emit('submit')"
      >
        <ArrowUpIcon class="h-3.5 w-3.5" />
      </button>
    </div>
    <p
      v-if="mode === 'answer'"
      class="mt-2 text-xs text-amber-700"
    >
      当前正在等待你回答上方问题，直接发送即可提交回答。
    </p>
    <p
      v-else-if="hint"
      class="mt-2 text-xs"
      :class="hintTone === 'warning' ? 'text-amber-700' : 'text-slate-500'"
    >
      {{ hint }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { ArrowUpIcon } from '@heroicons/vue/24/outline'

const props = withDefaults(defineProps<{
  modelValue: string
  disabled: boolean
  placeholder: string
  canSubmit: boolean
  mode?: 'chat' | 'answer' | 'blocked'
  hint?: string
  hintTone?: 'default' | 'warning'
}>(), {
  mode: 'chat',
  hint: '',
  hintTone: 'default',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
}>()

const onInput = (event: Event) => {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

const onEnter = (event: KeyboardEvent) => {
  if (event.isComposing || props.disabled || !props.canSubmit) return
  emit('submit')
}
</script>
