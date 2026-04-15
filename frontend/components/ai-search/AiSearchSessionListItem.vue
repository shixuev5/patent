<template>
  <div
    ref="rootRef"
    class="w-full rounded-xl border px-2.5 py-2 text-left transition"
    :class="active
      ? 'border-cyan-300 bg-cyan-50/70 shadow-sm shadow-cyan-100'
      : 'border-slate-200 bg-slate-50/50 hover:border-slate-300 hover:bg-white'"
  >
    <div class="flex items-center gap-2">
      <div class="min-w-0 flex-1">
        <div v-if="editing" class="space-y-2">
          <input
            ref="inputRef"
            v-model.trim="draftTitle"
            type="text"
            class="w-full rounded-xl border border-cyan-200 bg-white px-3 py-2 text-[13px] font-semibold text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
            maxlength="120"
            placeholder="输入会话标题"
            @keydown.enter.prevent="submitRename"
            @keydown.esc.prevent="cancelRename"
          />
          <div class="flex items-center justify-end gap-2">
            <button
              type="button"
              class="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-100"
              @click.stop="cancelRename"
            >
              取消
            </button>
            <button
              type="button"
              class="rounded-lg bg-cyan-700 px-2.5 py-1 text-[11px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              :disabled="busy || !canSubmitRename"
              @click.stop="submitRename"
            >
              保存
            </button>
          </div>
        </div>

        <button
          v-else
          type="button"
          class="block w-full text-left"
          :title="session.title"
          @click="$emit('select', session.sessionId)"
        >
          <div class="flex items-center gap-1.5">
            <p class="min-w-0 truncate whitespace-nowrap text-[12px] font-semibold leading-5 text-slate-900">
              {{ session.title }}
            </p>
            <span
              v-if="showActivityIndicator"
              class="inline-flex h-1.5 w-1.5 shrink-0 rounded-full"
              :class="activityIndicatorClass"
              :aria-label="activityIndicatorLabel"
              :title="activityIndicatorLabel"
            />
          </div>
        </button>
      </div>

      <div v-if="!editing" class="relative shrink-0">
        <button
          type="button"
          class="flex h-6 w-6 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:text-slate-300"
          :disabled="busy"
          aria-label="会话操作"
          @click.stop="toggleMenu"
        >
          <EllipsisHorizontalIcon class="h-3.5 w-3.5" />
        </button>

        <div
          v-if="menuOpen"
          class="absolute right-0 top-10 z-20 w-36 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-xl shadow-slate-200/80"
        >
          <button
            type="button"
            class="session-menu-btn"
            @click.stop="startRename"
          >
            <PencilSquareIcon class="h-4 w-4" />
            <span>重命名</span>
          </button>
          <button
            type="button"
            class="session-menu-btn"
            @click.stop="togglePinned"
          >
            <BookmarkSquareIcon class="h-4 w-4" />
            <span>{{ session.pinned ? '取消置顶' : '置顶' }}</span>
          </button>
          <button
            type="button"
            class="session-menu-btn text-rose-600 hover:bg-rose-50"
            @click.stop="confirmDelete"
          >
            <TrashIcon class="h-4 w-4" />
            <span>删除</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { BookmarkSquareIcon, EllipsisHorizontalIcon, PencilSquareIcon, TrashIcon } from '@heroicons/vue/24/outline'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AiSearchSessionSummary } from '~/types/aiSearch'

const props = defineProps<{
  session: AiSearchSessionSummary
  active: boolean
  busy?: boolean
}>()

const emit = defineEmits<{
  select: [sessionId: string]
  rename: [sessionId: string, title: string]
  togglePin: [sessionId: string, pinned: boolean]
  delete: [sessionId: string]
}>()

const rootRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)
const menuOpen = ref(false)
const editing = ref(false)
const draftTitle = ref(props.session.title)

const canSubmitRename = computed(() => {
  const nextTitle = draftTitle.value.trim()
  return !!nextTitle && nextTitle !== props.session.title
})
const activityState = computed(() => String(props.session.activityState || 'none').trim())
const showActivityIndicator = computed(() => activityState.value === 'running' || activityState.value === 'paused')
const activityIndicatorClass = computed(() => (
  activityState.value === 'running'
    ? 'bg-cyan-500 animate-pulse'
    : 'bg-amber-400'
))
const activityIndicatorLabel = computed(() => (
  activityState.value === 'running' ? '进行中' : '已暂停'
))

const syncDraft = () => {
  draftTitle.value = props.session.title
}

const closeMenu = () => {
  menuOpen.value = false
}

const toggleMenu = () => {
  menuOpen.value = !menuOpen.value
}

const startRename = async () => {
  closeMenu()
  editing.value = true
  syncDraft()
  await nextTick()
  inputRef.value?.focus()
  inputRef.value?.select()
}

const cancelRename = () => {
  editing.value = false
  syncDraft()
}

const submitRename = () => {
  const nextTitle = draftTitle.value.trim()
  if (!nextTitle || nextTitle === props.session.title) {
    editing.value = false
    syncDraft()
    return
  }
  editing.value = false
  emit('rename', props.session.sessionId, nextTitle)
}

const togglePinned = () => {
  closeMenu()
  emit('togglePin', props.session.sessionId, !props.session.pinned)
}

const confirmDelete = () => {
  closeMenu()
  emit('delete', props.session.sessionId)
}

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target as Node | null
  if (!target || !rootRef.value?.contains(target)) {
    closeMenu()
  }
}

watch(() => props.session.title, syncDraft)
watch(() => props.busy, (value) => {
  if (!value) return
  closeMenu()
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<style scoped>
.session-menu-btn {
  @apply flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-slate-700 transition hover:bg-slate-100;
}
</style>
