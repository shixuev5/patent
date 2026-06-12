<template>
  <section>
    <div class="relative">
      <textarea
        :value="modelValue"
        rows="2"
        class="min-h-[4.5rem] max-h-[8.5rem] w-full resize-none overflow-y-auto rounded-2xl border border-slate-200 bg-white px-4 py-3 pb-7 pr-14 text-[13px] leading-5 text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-50"
        :disabled="disabled"
        :placeholder="placeholder"
        @input="onInput"
        @keydown.enter.exact.prevent="onEnter"
        @keydown.meta.enter.prevent="$emit('submit')"
        @keydown.ctrl.enter.prevent="$emit('submit')"
      />
      <span class="pointer-events-none absolute bottom-2.5 left-4 text-[11px] text-slate-400">
        Enter 发送，Shift+Enter 换行
      </span>
      <button
        type="button"
        class="absolute bottom-3 right-3 inline-flex h-8 w-8 items-center justify-center rounded-full bg-cyan-700 text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
        aria-label="发送消息"
        :disabled="!canSubmit"
        @click="$emit('submit')"
      >
        <ArrowUpIcon class="h-3.5 w-3.5" />
      </button>
    </div>
    <div v-if="canSupplement" class="mt-2">
      <button
        type="button"
        class="inline-flex h-7 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 text-[12px] font-medium text-slate-600 transition hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-700 disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="supplementDisabled || supplementBusy"
        :title="supplementDisabled ? '当前轮次结束后可补充文献' : '补充公开号或 PDF 文献'"
        @click="supplementOpen = !supplementOpen"
      >
        <PaperClipIcon class="h-3.5 w-3.5" />
        <span>补充文献</span>
      </button>
      <div v-if="supplementOpen" class="mt-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3">
        <div class="grid gap-2 sm:grid-cols-[minmax(0,1fr),auto]">
          <input
            v-model.trim="supplementPatentNumbers"
            type="text"
            class="h-9 min-w-0 rounded-lg border border-slate-200 bg-white px-3 text-[12px] text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
            placeholder="公开号：CN107219079A、CN106596008B"
          />
          <button
            type="button"
            class="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[12px] font-medium text-slate-600 transition hover:border-cyan-200 hover:bg-white hover:text-cyan-700"
            @click="fileInputRef?.click()"
          >
            <PaperClipIcon class="h-3.5 w-3.5" />
            <span>选择 PDF</span>
          </button>
        </div>
        <input
          ref="fileInputRef"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          class="hidden"
          @change="onSupplementFilesChange"
        />
        <div v-if="supplementFiles.length" class="mt-2 flex flex-wrap gap-1.5">
          <span
            v-for="file in supplementFiles"
            :key="`${file.name}-${file.size}-${file.lastModified}`"
            class="inline-flex max-w-full items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600"
          >
            <span class="truncate">{{ file.name }}</span>
            <button
              type="button"
              class="text-slate-400 hover:text-rose-600"
              title="移除"
              @click="removeSupplementFile(file)"
            >
              <XMarkIcon class="h-3 w-3" />
            </button>
          </span>
        </div>
        <textarea
          v-model.trim="supplementReviewGoal"
          rows="2"
          class="mt-2 min-h-[3.75rem] w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-[12px] leading-5 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
          placeholder="筛查目标：对比当前检索目标，判断命中点、缺口和是否应选为对比文献"
        />
        <div class="mt-2 flex items-center justify-end gap-2">
          <button
            type="button"
            class="inline-flex h-8 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-[12px] font-medium text-slate-600 transition hover:bg-slate-100"
            :disabled="supplementBusy"
            @click="resetSupplementDraft"
          >
            清空
          </button>
          <button
            type="button"
            class="inline-flex h-8 items-center justify-center rounded-lg bg-cyan-700 px-3 text-[12px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="!canSubmitSupplement"
            @click="submitSupplement"
          >
            {{ supplementBusy ? '导入中...' : '导入并筛查' }}
          </button>
        </div>
      </div>
    </div>
    <p
      v-if="hint"
      class="mt-2 text-xs"
      :class="hintTone === 'warning' ? 'text-amber-700' : 'text-slate-500'"
    >
      {{ hint }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { ArrowUpIcon, PaperClipIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { computed, ref } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string
  disabled: boolean
  placeholder: string
  canSubmit: boolean
  mode?: 'chat' | 'blocked'
  hint?: string
  hintTone?: 'default' | 'warning'
  canSupplement?: boolean
  supplementDisabled?: boolean
  supplementBusy?: boolean
}>(), {
  mode: 'chat',
  hint: '',
  hintTone: 'default',
  canSupplement: false,
  supplementDisabled: false,
  supplementBusy: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  supplement: [payload: { patentNumbers: string, reviewGoal: string, files: File[] }]
  submit: []
}>()

const supplementOpen = ref(false)
const supplementPatentNumbers = ref('')
const supplementReviewGoal = ref('')
const supplementFiles = ref<File[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)

const canSubmitSupplement = computed(() => (
  props.canSupplement
  && !props.supplementDisabled
  && !props.supplementBusy
  && (
    !!supplementPatentNumbers.value.trim()
    || supplementFiles.value.length > 0
  )
))

const onInput = (event: Event) => {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}

const onEnter = (event: KeyboardEvent) => {
  if (event.isComposing || props.disabled || !props.canSubmit) return
  emit('submit')
}

const onSupplementFilesChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || []).filter(file => file.type === 'application/pdf' || /\.pdf$/i.test(file.name))
  const existing = new Map(supplementFiles.value.map(file => [`${file.name}-${file.size}-${file.lastModified}`, file]))
  files.forEach((file) => {
    existing.set(`${file.name}-${file.size}-${file.lastModified}`, file)
  })
  supplementFiles.value = Array.from(existing.values()).slice(0, 8)
  input.value = ''
}

const removeSupplementFile = (target: File) => {
  supplementFiles.value = supplementFiles.value.filter(file => file !== target)
}

const resetSupplementDraft = () => {
  supplementPatentNumbers.value = ''
  supplementReviewGoal.value = ''
  supplementFiles.value = []
}

const submitSupplement = () => {
  if (!canSubmitSupplement.value) return
  emit('supplement', {
    patentNumbers: supplementPatentNumbers.value.trim(),
    reviewGoal: supplementReviewGoal.value.trim(),
    files: [...supplementFiles.value],
  })
  resetSupplementDraft()
  supplementOpen.value = false
}
</script>
