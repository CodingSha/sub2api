<template>
  <AppLayout>
    <div class="space-y-3">
      <div class="card p-3">
        <div class="grid grid-cols-1 gap-3 2xl:grid-cols-[minmax(520px,0.9fr)_minmax(0,1.1fr)_auto]">
          <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <div
              v-for="item in overviewItems"
              :key="item.key"
              class="min-w-0 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 dark:border-dark-700 dark:bg-dark-900/40"
            >
              <div class="flex min-w-0 items-center gap-2">
                <div class="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg" :class="item.iconClass">
                  <Icon :name="item.icon" size="xs" />
                </div>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-[11px] font-medium leading-4 text-gray-500 dark:text-gray-400">{{ item.label }}</p>
                  <div class="flex min-w-0 items-baseline gap-1.5">
                    <p class="truncate text-base font-semibold leading-5 text-gray-900 dark:text-white">{{ item.value }}</p>
                    <p v-if="item.meta" class="truncate text-[11px] text-gray-500 dark:text-gray-400">{{ item.meta }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
            <div>
              <label class="mb-1 block text-[11px] font-medium text-gray-500 dark:text-gray-400">{{ t('admin.audit.search') }}</label>
              <div class="relative">
                <Icon name="search" size="sm" class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  v-model="filters.keyword"
                  type="search"
                  class="input h-9 w-full pl-9 text-sm"
                  :placeholder="t('admin.audit.searchPlaceholder')"
                />
              </div>
            </div>
            <div>
              <label class="mb-1 block text-[11px] font-medium text-gray-500 dark:text-gray-400">{{ t('admin.audit.platform') }}</label>
              <select v-model="filters.platform" class="input h-9 w-full text-sm">
                <option v-for="option in platformOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
              </select>
            </div>
            <div>
              <label class="mb-1 block text-[11px] font-medium text-gray-500 dark:text-gray-400">{{ t('admin.audit.model') }}</label>
              <input v-model="filters.model" type="text" class="input h-9 w-full text-sm" :placeholder="t('admin.audit.modelPlaceholder')" />
            </div>
            <div>
              <label class="mb-1 block text-[11px] font-medium text-gray-500 dark:text-gray-400">{{ t('admin.audit.endpoint') }}</label>
              <input v-model="filters.endpoint" type="text" class="input h-9 w-full text-sm" :placeholder="t('admin.audit.endpointPlaceholder')" />
            </div>
          </div>

          <div class="flex items-end gap-2 2xl:justify-end">
            <button type="button" class="btn btn-secondary inline-flex h-9 items-center gap-2 px-3 text-sm" @click="resetFilters">
              <Icon name="refresh" size="sm" />
              {{ t('admin.audit.reset') }}
            </button>
            <button type="button" class="btn btn-primary inline-flex h-9 items-center gap-2 px-3 text-sm" :disabled="loading" @click="loadRows">
              <Icon name="refresh" size="sm" />
              {{ t('admin.audit.refresh') }}
            </button>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(440px,2fr)]">
        <div class="card flex h-[calc(100vh-8rem)] min-h-[560px] flex-col overflow-hidden">
          <div class="flex flex-shrink-0 items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-dark-700">
            <div>
              <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('admin.audit.records') }}</h2>
              <p class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{{ t('admin.audit.recordCount', { count: pagination.total }) }}</p>
            </div>
            <span class="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600 dark:bg-dark-700 dark:text-gray-300">
              {{ t('admin.audit.retentionBadge') }}
            </span>
          </div>

          <div v-if="errorMessage" class="flex-shrink-0 border-b border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900/40 dark:bg-rose-900/20 dark:text-rose-200">
            {{ errorMessage }}
          </div>

          <div class="min-h-0 flex-1 overflow-auto">
            <table class="min-w-full divide-y divide-gray-200 dark:divide-dark-700">
              <thead class="bg-gray-50 dark:bg-dark-800/80">
                <tr>
                  <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('admin.audit.time') }}</th>
                  <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('admin.audit.user') }}</th>
                  <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('admin.audit.platform') }}</th>
                  <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('admin.audit.model') }}</th>
                  <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('admin.audit.session') }}</th>
                  <th class="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">{{ t('admin.audit.statusCode') }}</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 bg-white dark:divide-dark-700 dark:bg-dark-800">
                <tr v-if="loading">
                  <td colspan="6" class="px-4 py-14 text-center text-sm text-gray-500 dark:text-gray-400">
                    {{ t('admin.audit.loading') }}
                  </td>
                </tr>
                <tr v-else-if="rows.length === 0">
                  <td colspan="6" class="px-4 py-14 text-center">
                    <div class="mx-auto flex max-w-sm flex-col items-center">
                      <div class="flex h-12 w-12 items-center justify-center rounded-lg bg-gray-100 text-gray-500 dark:bg-dark-700 dark:text-gray-300">
                        <Icon name="inbox" size="lg" />
                      </div>
                      <p class="mt-4 text-sm font-medium text-gray-900 dark:text-white">{{ t('admin.audit.emptyTitle') }}</p>
                      <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{{ t('admin.audit.emptyDescription') }}</p>
                    </div>
                  </td>
                </tr>
                <tr
                  v-for="row in rows"
                  :key="row.id"
                  class="cursor-pointer transition-colors hover:bg-gray-50 dark:hover:bg-dark-700/50"
                  :class="{ 'bg-primary-50/70 dark:bg-primary-900/10': selectedRow?.id === row.id }"
                  @click="selectedRow = row"
                >
                  <td class="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-300">{{ row.time }}</td>
                  <td class="px-4 py-3">
                    <div class="min-w-0">
                      <p class="truncate text-sm font-medium text-gray-900 dark:text-white">{{ row.user }}</p>
                      <p class="truncate text-xs text-gray-500 dark:text-gray-400">{{ row.apiKey }}</p>
                    </div>
                  </td>
                  <td class="whitespace-nowrap px-4 py-3 text-sm text-gray-600 dark:text-gray-300">{{ row.platform }}</td>
                  <td class="whitespace-nowrap px-4 py-3 font-mono text-xs text-gray-600 dark:text-gray-300">{{ row.model }}</td>
                  <td class="px-4 py-3">
                    <div class="min-w-0">
                      <p class="truncate font-mono text-xs text-gray-600 dark:text-gray-300">{{ row.sessionId }}</p>
                      <p class="truncate text-xs text-gray-500 dark:text-gray-400">{{ t('admin.audit.turnCount', { count: row.requestCount }) }}</p>
                    </div>
                  </td>
                  <td class="whitespace-nowrap px-4 py-3">
                    <span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium" :class="statusClass(row.statusCode)">
                      {{ row.statusCode || '-' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="flex flex-shrink-0 flex-col gap-3 border-t border-gray-100 px-4 py-3 text-sm dark:border-dark-700 sm:flex-row sm:items-center sm:justify-between">
            <div class="text-gray-500 dark:text-gray-400">
              {{ t('admin.audit.pageSummary', { page: pagination.page, pages: pagination.pages }) }}
            </div>
            <div class="flex items-center gap-2">
              <button type="button" class="btn btn-secondary px-3 py-1.5 text-xs" :disabled="loading || pagination.page <= 1" @click="goPage(pagination.page - 1)">
                {{ t('admin.audit.previousPage') }}
              </button>
              <button type="button" class="btn btn-secondary px-3 py-1.5 text-xs" :disabled="loading || pagination.page >= pagination.pages" @click="goPage(pagination.page + 1)">
                {{ t('admin.audit.nextPage') }}
              </button>
            </div>
          </div>
        </div>

        <aside class="card flex h-[calc(100vh-8rem)] min-h-[560px] flex-col overflow-hidden">
          <div class="flex-shrink-0 border-b border-gray-100 px-4 py-3 dark:border-dark-700">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <h2 class="text-base font-semibold text-gray-900 dark:text-white">{{ t('admin.audit.detail') }}</h2>
                <p class="mt-0.5 truncate font-mono text-xs text-gray-500 dark:text-gray-400">
                  {{ selectedRow?.sessionId || selectedRow?.requestId || t('admin.audit.noSelection') }}
                </p>
              </div>
              <span
                v-if="selectedRow"
                class="inline-flex flex-shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium"
                :class="statusClass(selectedRow.statusCode)"
              >
                {{ selectedRow.statusCode || '-' }}
              </span>
            </div>
            <div v-if="selectedRow" class="mt-3 flex flex-wrap gap-2 text-xs">
              <span class="inline-flex max-w-full items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-gray-600 dark:bg-dark-700 dark:text-gray-300">
                <Icon name="database" size="xs" />
                <span class="truncate">{{ selectedRow.account }}</span>
              </span>
              <span class="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-gray-600 dark:bg-dark-700 dark:text-gray-300">
                <Icon name="chat" size="xs" />
                {{ t('admin.audit.turnCount', { count: selectedRow.requestCount }) }}
              </span>
              <span class="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-gray-600 dark:bg-dark-700 dark:text-gray-300">
                <Icon name="clock" size="xs" />
                {{ selectedRow.latency }}
              </span>
              <span class="inline-flex max-w-full items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-gray-600 dark:bg-dark-700 dark:text-gray-300">
                <Icon name="globe" size="xs" />
                <span class="truncate">{{ selectedRow.ipAddress }}</span>
              </span>
            </div>
          </div>
          <div
            v-if="selectedRow"
            ref="conversationViewport"
            class="min-h-0 flex-1 overflow-y-auto bg-gray-50/70 px-4 py-4 dark:bg-dark-900/40"
            @scroll.passive="handleConversationScroll"
          >
            <div v-if="selectedTurns.length === 0" class="flex min-h-full items-center justify-center px-4 text-center">
              <div>
                <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-white text-gray-500 shadow-sm dark:bg-dark-800 dark:text-dark-300">
                  <Icon name="chat" size="lg" />
                </div>
                <p class="mt-4 text-sm font-medium text-gray-900 dark:text-white">{{ t('admin.audit.emptyConversation') }}</p>
              </div>
            </div>
            <div v-else class="mx-auto flex max-w-3xl flex-col gap-4">
              <div v-if="allConversationTurnsLoaded" class="flex justify-center">
                <span class="rounded-full bg-white px-3 py-1 text-xs text-gray-500 shadow-sm dark:bg-dark-800 dark:text-dark-300">
                  {{ t('admin.audit.noMoreTurns') }}
                </span>
              </div>
              <div v-else class="flex justify-center">
                <button
                  type="button"
                  class="inline-flex items-center gap-1 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-gray-600 shadow-sm transition hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/40 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-dark-800 dark:text-dark-300 dark:hover:bg-dark-700"
                  :disabled="loadingOlderTurns"
                  @click="loadOlderConversationTurns"
                >
                  <Icon name="chevronUp" size="xs" />
                  {{ loadingOlderTurns ? t('admin.audit.loadingEarlierTurns') : t('admin.audit.loadEarlierTurns') }}
                </button>
              </div>

              <div v-for="turn in visibleConversationTurns" :key="turn.key" class="space-y-3">
                <div class="flex items-center gap-3">
                  <div class="h-px flex-1 bg-gray-200 dark:bg-dark-700"></div>
                  <span class="max-w-[70%] truncate rounded-full bg-white px-3 py-1 text-xs text-gray-500 shadow-sm dark:bg-dark-800 dark:text-dark-300">
                    {{ turn.title }}
                  </span>
                  <div class="h-px flex-1 bg-gray-200 dark:bg-dark-700"></div>
                </div>

                <div
                  v-for="message in turn.messages"
                  :key="message.key"
                  class="flex gap-2"
                  :class="message.kind === 'user' ? 'justify-end' : 'justify-start'"
                >
                  <div
                    v-if="message.kind !== 'user'"
                    class="mt-6 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg"
                    :class="messageIconClass(message.kind)"
                  >
                    <Icon :name="messageIcon(message.kind)" size="sm" />
                  </div>
                  <div class="max-w-[86%]">
                    <div
                      class="mb-1 flex gap-1 text-xs font-medium"
                      :class="message.kind === 'user' ? 'justify-end text-primary-700 dark:text-primary-300' : messageLabelClass(message.kind)"
                    >
                      <span>{{ messageLabel(message.kind) }}</span>
                      <span v-if="message.truncated">· {{ t('admin.audit.truncated') }}</span>
                    </div>
                    <pre
                      class="whitespace-pre-wrap break-words rounded-lg px-3.5 py-3 text-sm leading-6 shadow-sm"
                      :class="messageBubbleClass(message.kind)"
                    >{{ message.text }}</pre>
                  </div>
                  <div
                    v-if="message.kind === 'user'"
                    class="mt-6 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-primary-100 text-primary-700 dark:bg-primary-900/40 dark:text-primary-200"
                  >
                    <Icon name="user" size="sm" />
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="flex min-h-[360px] flex-1 items-center justify-center px-6 text-center">
            <div>
              <div class="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-gray-100 text-gray-500 dark:bg-dark-700 dark:text-gray-300">
                <Icon name="clipboard" size="lg" />
              </div>
              <p class="mt-4 text-sm font-medium text-gray-900 dark:text-white">{{ t('admin.audit.noSelectionTitle') }}</p>
              <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{{ t('admin.audit.noSelectionDescription') }}</p>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </AppLayout>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppLayout from '@/components/layout/AppLayout.vue'
import Icon from '@/components/icons/Icon.vue'
import auditAPI, { type AuditLog } from '@/api/admin/audit'

interface AuditContentItem {
  request_id?: string
  model?: string
  content?: unknown
  truncated?: boolean
  created_at?: string
}

type AuditMessageKind = 'user' | 'assistant' | 'tool_call' | 'tool_result'

interface AuditConversationMessage {
  key: string
  kind: AuditMessageKind
  text: string
  truncated: boolean
}

interface AuditTurn {
  key: string
  title: string
  messages: AuditConversationMessage[]
}

interface AuditRow {
  id: string
  time: string
  user: string
  apiKey: string
  account: string
  platform: string
  model: string
  endpoint: string
  sessionId: string
  requestCount: number
  statusCode: number
  requestId: string
  latency: string
  ipAddress: string
  requestTruncated: boolean
  responseTruncated: boolean
  turns: AuditTurn[]
}

const { t } = useI18n()
const CONVERSATION_TURN_PAGE_SIZE = 10

const filters = reactive({
  keyword: '',
  platform: 'all',
  model: '',
  endpoint: '',
})

const rows = ref<AuditRow[]>([])
const selectedRow = ref<AuditRow | null>(null)
const loading = ref(false)
const loadingOlderTurns = ref(false)
const errorMessage = ref('')
const conversationViewport = ref<HTMLElement | null>(null)
const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
  pages: 1,
})
const visibleTurnCount = ref(CONVERSATION_TURN_PAGE_SIZE)
let activeController: AbortController | null = null

const overviewItems = computed(() => [
  {
    key: 'total',
    label: t('admin.audit.totalRecords'),
    value: pagination.total.toLocaleString(),
    meta: t('admin.audit.allModels'),
    icon: 'clipboard' as const,
    iconClass: 'bg-sky-50 text-sky-600 dark:bg-sky-900/20 dark:text-sky-300',
  },
  {
    key: 'success',
    label: t('admin.audit.successful'),
    value: rows.value.filter((row) => row.statusCode >= 200 && row.statusCode < 300).length.toLocaleString(),
    meta: t('admin.audit.currentPage'),
    icon: 'eye' as const,
    iconClass: 'bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-300',
  },
  {
    key: 'errors',
    label: t('admin.audit.failed'),
    value: rows.value.filter((row) => row.statusCode >= 400).length.toLocaleString(),
    meta: t('admin.audit.currentPage'),
    icon: 'shield' as const,
    iconClass: 'bg-rose-50 text-rose-600 dark:bg-rose-900/20 dark:text-rose-300',
  },
  {
    key: 'coverage',
    label: t('admin.audit.coverage'),
    value: rows.value.length > 0 ? '100%' : '0%',
    meta: t('admin.audit.captured'),
    icon: 'database' as const,
    iconClass: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/20 dark:text-emerald-300',
  },
])

const platformOptions = computed(() => [
  { value: 'all', label: t('admin.audit.allPlatforms') },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Claude / Anthropic' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'antigravity', label: 'Antigravity' },
])

const selectedTurns = computed(() => selectedRow.value?.turns || [])
const visibleConversationTurns = computed(() => {
  const count = Math.max(CONVERSATION_TURN_PAGE_SIZE, visibleTurnCount.value)
  return selectedTurns.value.slice(Math.max(0, selectedTurns.value.length - count))
})
const allConversationTurnsLoaded = computed(() => visibleConversationTurns.value.length >= selectedTurns.value.length)

function resetFilters() {
  filters.keyword = ''
  filters.platform = 'all'
  filters.model = ''
  filters.endpoint = ''
  pagination.page = 1
  loadRows()
}

function statusClass(statusCode: number) {
  if (statusCode >= 500) return 'bg-rose-50 text-rose-700 dark:bg-rose-900/20 dark:text-rose-300'
  if (statusCode >= 400) return 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300'
  if (statusCode >= 200 && statusCode < 300) return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-300'
  return 'bg-gray-100 text-gray-700 dark:bg-dark-700 dark:text-gray-300'
}

function messageLabel(kind: AuditMessageKind) {
  if (kind === 'user') return t('admin.audit.userMessage')
  if (kind === 'tool_call') return t('admin.audit.toolCallMessage')
  if (kind === 'tool_result') return t('admin.audit.toolResultMessage')
  return t('admin.audit.assistantMessage')
}

function messageIcon(kind: AuditMessageKind): 'sparkles' | 'terminal' | 'clipboard' {
  if (kind === 'tool_call') return 'terminal'
  if (kind === 'tool_result') return 'clipboard'
  return 'sparkles'
}

function messageIconClass(kind: AuditMessageKind) {
  if (kind === 'tool_call') return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200'
  if (kind === 'tool_result') return 'bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-200'
  return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200'
}

function messageLabelClass(kind: AuditMessageKind) {
  if (kind === 'tool_call') return 'text-amber-700 dark:text-amber-300'
  if (kind === 'tool_result') return 'text-sky-700 dark:text-sky-300'
  return 'text-emerald-700 dark:text-emerald-300'
}

function messageBubbleClass(kind: AuditMessageKind) {
  if (kind === 'user') return 'bg-primary-600 text-white'
  if (kind === 'tool_call') return 'border border-amber-200 bg-amber-50 font-mono text-xs text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100'
  if (kind === 'tool_result') return 'border border-sky-200 bg-sky-50 font-mono text-xs text-sky-950 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-100'
  return 'border border-gray-200 bg-white text-gray-800 dark:border-dark-700 dark:bg-dark-800 dark:text-gray-100'
}

function formatTime(value: string) {
  if (!value) return '-'
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function prettifyJSON(value: string) {
  if (!value) return ''
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

function contentToText(value: unknown) {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function extractTextFromContent(value: unknown): string[] {
  if (value === undefined || value === null) return []
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) {
    return value.flatMap((item) => extractTextFromContent(item))
  }
  if (typeof value === 'object') {
    const item = value as Record<string, unknown>
    const type = typeof item.type === 'string' ? item.type : ''
    if (typeof item.text === 'string' && (!type || type.includes('text') || type === 'input')) return [item.text]
    if (typeof item.content === 'string') return [item.content]
    if (Array.isArray(item.content)) return extractTextFromContent(item.content)
    if (Array.isArray(item.parts)) return extractTextFromContent(item.parts)
  }
  return []
}

function extractSessionTagText(value: string): string {
  const match = value.match(/<session>\s*([\s\S]*?)\s*<\/session>/)
  return match?.[1]?.trim() || ''
}

function isAuditUserText(value: string) {
  const text = value.trim()
  if (!text) return false
  if (text.startsWith('<system-reminder>')) return false
  if (text.startsWith('<command-message>')) return false
  if (text.startsWith('<local-command-stdout>')) return false
  if (text.startsWith('<tool_use_id>')) return false
  if (text.startsWith('x-anthropic-billing-header:')) return false
  if (text.startsWith("You are Claude Code, Anthropic's official CLI for Claude.")) return false
  if (text.startsWith('You are an interactive agent that helps users with software engineering tasks.')) return false
  if (text.includes('The following skills are available for use with the Skill tool')) return false
  return true
}

function cleanAuditUserText(value: string) {
  return value
    .replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '')
    .replace(/<command-message>[\s\S]*?<\/command-message>/g, '')
    .replace(/<local-command-stdout>[\s\S]*?<\/local-command-stdout>/g, '')
    .trim()
}

function lastUsefulUserText(values: string[]) {
  const cleaned = values.map(cleanAuditUserText).filter(isAuditUserText)
  return cleaned[cleaned.length - 1] || ''
}

function extractEscapedJSONTextFields(value: string) {
  const texts: string[] = []
  const searchable = value
    .split(',"system":[')[0]
    .split(',\\"system\\":[')[0]
  const patterns = [
    /\\"text\\":\\"((?:\\\\.|[^"\\])*)\\"/g,
    /"text"\s*:\s*"((?:\\.|[^"\\])*)"/g,
  ]
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(searchable)) !== null) {
      try {
        texts.push(JSON.parse(`"${match[1]}"`))
      } catch {
        texts.push(match[1].replace(/\\n/g, '\n').replace(/\\"/g, '"'))
      }
    }
  }
  return texts
}

function extractEscapedJSONUserTextFields(value: string) {
  const texts: string[] = []
  const searchable = value
    .split(',"system":[')[0]
    .split(',\\"system\\":[')[0]
  const patterns = [
    /\{\\"role\\":\\"user\\"[\s\S]*?\\"content\\":\[(?:[\s\S]*?\\"text\\":\\"((?:\\\\.|[^"\\])*)\\")+[\s\S]*?\]/g,
    /\{"role"\s*:\s*"user"[\s\S]*?"content"\s*:\s*\[(?:[\s\S]*?"text"\s*:\s*"((?:\\.|[^"\\])*)")+[\s\S]*?\]/g,
  ]
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(searchable)) !== null) {
      if (!match[1]) continue
      try {
        texts.push(JSON.parse(`"${match[1]}"`))
      } catch {
        texts.push(match[1].replace(/\\n/g, '\n').replace(/\\"/g, '"'))
      }
    }
  }
  return texts
}

function extractEscapedJSONContentFields(value: string) {
  const texts: string[] = []
  const searchable = value
    .split(',"system":[')[0]
    .split(',\\"system\\":[')[0]
  const patterns = [
    /\\"content\\":\\"((?:\\\\.|[^"\\])*)\\"/g,
    /"content"\s*:\s*"((?:\\.|[^"\\])*)"/g,
  ]
  for (const pattern of patterns) {
    let match: RegExpExecArray | null
    while ((match = pattern.exec(searchable)) !== null) {
      const before = searchable.slice(Math.max(0, match.index - 80), match.index)
      if (before.includes('\\"role\\":\\"assistant') || before.includes('"role":"assistant')) continue
      try {
        texts.push(JSON.parse(`"${match[1]}"`))
      } catch {
        texts.push(match[1].replace(/\\n/g, '\n').replace(/\\"/g, '"'))
      }
    }
  }
  return texts
}

function extractUserInputText(value: unknown): string {
  const raw = contentToText(value)
  if (!raw.trim()) return ''
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const messages = Array.isArray(parsed.messages) ? parsed.messages : Array.isArray(parsed.input) ? parsed.input : []
    const messageTexts = messages.flatMap((message) => {
      if (!message || typeof message !== 'object') return []
      const item = message as Record<string, unknown>
      if (item.role !== 'user') return []
      return extractTextFromContent(item.content)
    })
    const lastMessageText = lastUsefulUserText(messageTexts)
    if (lastMessageText) return lastMessageText

    const contents = Array.isArray(parsed.contents) ? parsed.contents : []
    const contentTexts = contents.flatMap((content) => {
      if (!content || typeof content !== 'object') return []
      const item = content as Record<string, unknown>
      if (item.role !== 'user') return []
      return extractTextFromContent(item.parts)
    })
    const lastContentText = lastUsefulUserText(contentTexts)
    if (lastContentText) return lastContentText

    const promptTexts = extractTextFromContent(parsed.prompt)
    const lastPromptText = lastUsefulUserText(promptTexts)
    if (lastPromptText) return lastPromptText

    const sessionText = extractSessionTagText(raw)
    if (sessionText) return sessionText
  } catch {
    const lastTextField = lastUsefulUserText([
      ...extractEscapedJSONUserTextFields(raw),
      ...extractEscapedJSONTextFields(raw),
      ...extractEscapedJSONContentFields(raw),
    ])
    if (lastTextField) return lastTextField
    const sessionText = extractSessionTagText(raw)
    if (sessionText) return sessionText
  }
  const lastTextField = lastUsefulUserText([
    ...extractEscapedJSONUserTextFields(raw),
    ...extractEscapedJSONTextFields(raw),
    ...extractEscapedJSONContentFields(raw),
  ])
  if (lastTextField) return lastTextField
  return cleanAuditUserText(raw)
}

function parseMaybeJSON(value: unknown): unknown | null {
  if (value && typeof value === 'object') return value
  if (typeof value !== 'string') return null
  const raw = value.trim()
  if (!raw || (!raw.startsWith('{') && !raw.startsWith('['))) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

function makeConversationMessage(
  kind: AuditMessageKind,
  text: string,
  truncated: boolean,
  key: string,
): AuditConversationMessage | null {
  const raw = text.trim()
  const cleaned = kind === 'user' && !looksLikeToolOutput(raw) ? cleanAuditUserText(raw) : raw
  if (!cleaned) return null
  if (kind === 'user' && !looksLikeToolOutput(raw) && !isAuditUserText(cleaned)) return null
  return { key, kind, text: cleaned, truncated }
}

function pushConversationMessage(
  messages: AuditConversationMessage[],
  kind: AuditMessageKind,
  text: string,
  truncated: boolean,
  key: string,
) {
  const message = makeConversationMessage(kind, text, truncated, key)
  if (message) messages.push(message)
}

function toolCallText(item: Record<string, unknown>): string {
  const type = typeof item.type === 'string' ? item.type : 'tool_call'
  const id =
    typeof item.id === 'string'
      ? item.id
      : typeof item.call_id === 'string'
        ? item.call_id
        : typeof item.tool_call_id === 'string'
          ? item.tool_call_id
          : ''
  const name =
    typeof item.name === 'string'
      ? item.name
      : typeof item.tool_name === 'string'
        ? item.tool_name
        : objectFieldString(item.function, 'name') || objectFieldString(item.functionCall, 'name') || objectFieldString(item.function_call, 'name')
  const rawInput =
    item.input
    ?? item.arguments
    ?? objectField(item.function, 'arguments')
    ?? objectField(item.functionCall, 'args')
    ?? objectField(item.functionCall, 'arguments')
    ?? objectField(item.function_call, 'arguments')
  const lines = [
    name ? `${t('admin.audit.toolName')}: ${name}` : '',
    id ? `${t('admin.audit.toolId')}: ${id}` : '',
    type ? `${t('admin.audit.toolType')}: ${type}` : '',
  ].filter(Boolean)
  const inputText = contentToText(rawInput)
  return inputText ? `${lines.join('\n')}\n\n${inputText}` : lines.join('\n')
}

function toolResultText(item: Record<string, unknown>): string {
  const type = typeof item.type === 'string' ? item.type : 'tool_result'
  const id =
    typeof item.tool_use_id === 'string'
      ? item.tool_use_id
      : typeof item.tool_call_id === 'string'
        ? item.tool_call_id
        : typeof item.call_id === 'string'
          ? item.call_id
          : ''
  const isError = item.is_error === true || item.status === 'error'
  const rawOutput =
    item.content
    ?? item.output
    ?? item.result
    ?? item.text
    ?? objectField(item.functionResponse, 'response')
    ?? objectField(item.function_response, 'response')
  const lines = [
    id ? `${t('admin.audit.toolId')}: ${id}` : '',
    type ? `${t('admin.audit.toolType')}: ${type}` : '',
    isError ? `${t('admin.audit.toolStatus')}: ${t('admin.audit.toolError')}` : '',
  ].filter(Boolean)
  const outputText = extractTextFromContent(rawOutput).join('\n') || contentToText(rawOutput)
  return outputText ? `${lines.join('\n')}\n\n${outputText}` : lines.join('\n')
}

function objectField(value: unknown, key: string): unknown {
  return value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined
}

function objectFieldString(value: unknown, key: string): string {
  const field = objectField(value, key)
  return typeof field === 'string' ? field : ''
}

function contentBlockKind(block: Record<string, unknown>, role: string): AuditMessageKind | null {
  const type = typeof block.type === 'string' ? block.type.toLowerCase() : ''
  if ([
    'tool_use',
    'server_tool_use',
    'tool_call',
    'function_call',
    'custom_tool_call',
    'mcp_tool_call',
  ].includes(type) || block.functionCall || block.function_call) {
    return 'tool_call'
  }
  if ([
    'tool_result',
    'tool_use_result',
    'web_search_tool_result',
    'function_call_output',
    'custom_tool_call_output',
    'mcp_tool_call_output',
  ].includes(type) || block.functionResponse || block.function_response) {
    return 'tool_result'
  }
  if (role === 'assistant' || role === 'model') return 'assistant'
  if (role === 'tool') return 'tool_result'
  return 'user'
}

function messagesFromContent(
  value: unknown,
  role: string,
  truncated: boolean,
  keyPrefix: string,
): AuditConversationMessage[] {
  const messages: AuditConversationMessage[] = []
  if (value === undefined || value === null) return messages
  if (typeof value === 'string') {
    const kind: AuditMessageKind = role === 'assistant' || role === 'model' ? 'assistant' : role === 'tool' ? 'tool_result' : 'user'
    pushConversationMessage(messages, kind, value, truncated, `${keyPrefix}-text`)
    return messages
  }
  if (Array.isArray(value)) {
    value.forEach((block, index) => {
      if (block && typeof block === 'object') {
        const obj = block as Record<string, unknown>
        const kind = contentBlockKind(obj, role)
        if (kind === 'tool_call') {
          pushConversationMessage(messages, kind, toolCallText(obj), truncated, `${keyPrefix}-tool-call-${index}`)
          return
        }
        if (kind === 'tool_result') {
          pushConversationMessage(messages, kind, toolResultText(obj), truncated, `${keyPrefix}-tool-result-${index}`)
          return
        }
        const text = extractTextFromContent(obj).join('\n')
        if (kind) pushConversationMessage(messages, kind, text, truncated, `${keyPrefix}-${kind}-${index}`)
        return
      }
      pushConversationMessage(
        messages,
        role === 'assistant' || role === 'model' ? 'assistant' : 'user',
        contentToText(block),
        truncated,
        `${keyPrefix}-item-${index}`,
      )
    })
    return messages
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>
    const kind = contentBlockKind(obj, role)
    if (kind === 'tool_call') {
      pushConversationMessage(messages, kind, toolCallText(obj), truncated, `${keyPrefix}-tool-call`)
      return messages
    }
    if (kind === 'tool_result') {
      pushConversationMessage(messages, kind, toolResultText(obj), truncated, `${keyPrefix}-tool-result`)
      return messages
    }
    const text = extractTextFromContent(obj).join('\n')
    if (kind) pushConversationMessage(messages, kind, text, truncated, `${keyPrefix}-${kind}`)
  }
  return messages
}

function messagesFromChatMessage(message: Record<string, unknown>, truncated: boolean, keyPrefix: string) {
  const role = typeof message.role === 'string' ? message.role.toLowerCase() : ''
  if (role === 'tool') {
    const messageText = toolResultText({
      type: 'tool_result',
      tool_call_id: message.tool_call_id,
      call_id: message.call_id,
      content: message.content,
    })
    const toolMessage = makeConversationMessage('tool_result', messageText, truncated, `${keyPrefix}-tool`)
    return toolMessage ? [toolMessage] : []
  }

  const messages = messagesFromContent(message.content ?? message.parts, role, truncated, keyPrefix)
  const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls : []
  toolCalls.forEach((toolCall, index) => {
    if (toolCall && typeof toolCall === 'object') {
      pushConversationMessage(messages, 'tool_call', toolCallText(toolCall as Record<string, unknown>), truncated, `${keyPrefix}-tool-calls-${index}`)
    }
  })
  if (message.function_call && typeof message.function_call === 'object') {
    pushConversationMessage(messages, 'tool_call', toolCallText(message.function_call as Record<string, unknown>), truncated, `${keyPrefix}-function-call`)
  }
  return messages
}

function latestMessagesFromChat(messages: unknown, truncated: boolean, keyPrefix: string): AuditConversationMessage[] {
  if (!Array.isArray(messages) || messages.length === 0) return []
  const lastIndex = messages.length - 1
  const last = messages[lastIndex]
  if (!last || typeof last !== 'object') return []
  const lastObj = last as Record<string, unknown>
  const lastMessages = messagesFromChatMessage(lastObj, truncated, `${keyPrefix}-${lastIndex}`)
  const hasToolResult = lastMessages.some((message) => message.kind === 'tool_result')
  if (!hasToolResult) return lastMessages

  const out: AuditConversationMessage[] = []
  for (let index = lastIndex - 1; index >= 0; index--) {
    const previous = messages[index]
    if (!previous || typeof previous !== 'object') continue
    const previousMessages = messagesFromChatMessage(previous as Record<string, unknown>, truncated, `${keyPrefix}-${index}`)
    const toolCalls = previousMessages.filter((message) => message.kind === 'tool_call')
    if (toolCalls.length > 0) {
      out.push(...toolCalls)
      break
    }
  }
  out.push(...lastMessages)
  return out
}

function messagesFromResponsesInput(input: unknown, truncated: boolean, keyPrefix: string): AuditConversationMessage[] {
  const items = Array.isArray(input) ? input : input ? [input] : []
  if (items.length === 0) return []
  const last = items[items.length - 1]
  const messages = responseInputItemMessages(last, truncated, `${keyPrefix}-${items.length - 1}`)
  const hasToolResult = messages.some((message) => message.kind === 'tool_result')
  if (!hasToolResult) return messages
  const out: AuditConversationMessage[] = []
  for (let index = items.length - 2; index >= 0; index--) {
    const previousMessages = responseInputItemMessages(items[index], truncated, `${keyPrefix}-${index}`)
    const toolCalls = previousMessages.filter((message) => message.kind === 'tool_call')
    if (toolCalls.length > 0) {
      out.push(...toolCalls)
      break
    }
  }
  out.push(...messages)
  return out
}

function responseInputItemMessages(item: unknown, truncated: boolean, keyPrefix: string): AuditConversationMessage[] {
  if (!item || typeof item !== 'object') return []
  const obj = item as Record<string, unknown>
  const type = typeof obj.type === 'string' ? obj.type.toLowerCase() : ''
  const role = typeof obj.role === 'string' ? obj.role.toLowerCase() : ''
  if (type === 'message' || role) {
    return messagesFromContent(obj.content ?? obj.text, role || 'user', truncated, keyPrefix)
  }
  if (contentBlockKind(obj, role) === 'tool_call') {
    const message = makeConversationMessage('tool_call', toolCallText(obj), truncated, `${keyPrefix}-tool-call`)
    return message ? [message] : []
  }
  if (contentBlockKind(obj, role) === 'tool_result') {
    const message = makeConversationMessage('tool_result', toolResultText(obj), truncated, `${keyPrefix}-tool-result`)
    return message ? [message] : []
  }
  if (type === 'input_text') {
    const message = makeConversationMessage('user', contentToText(obj.text), truncated, `${keyPrefix}-input-text`)
    return message ? [message] : []
  }
  if (type === 'output_text') {
    const message = makeConversationMessage('assistant', contentToText(obj.text), truncated, `${keyPrefix}-output-text`)
    return message ? [message] : []
  }
  return []
}

function conversationMessagesFromPayload(value: unknown, fallbackKind: AuditMessageKind, truncated: boolean, keyPrefix: string) {
  const parsed = parseMaybeJSON(value)
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const payload = parsed as Record<string, unknown>
    if (Array.isArray(payload.audit_events)) {
      const messages: AuditConversationMessage[] = []
      payload.audit_events.forEach((event, index) => {
        if (!event || typeof event !== 'object') return
        const obj = event as Record<string, unknown>
        const kind = obj.kind === 'tool_call' || obj.kind === 'tool_result' || obj.kind === 'assistant' || obj.kind === 'user'
          ? obj.kind
          : fallbackKind
        let text = contentToText(obj.text ?? obj.content)
        if (!text && kind === 'tool_call') text = toolCallText(obj)
        if (!text && kind === 'tool_result') text = toolResultText(obj)
        pushConversationMessage(messages, kind as AuditMessageKind, text, truncated, `${keyPrefix}-audit-event-${index}`)
      })
      return messages
    }
    if (Array.isArray(payload.messages)) return latestMessagesFromChat(payload.messages, truncated, `${keyPrefix}-messages`)
    if (Array.isArray(payload.input) || payload.input) return messagesFromResponsesInput(payload.input, truncated, `${keyPrefix}-input`)
    if (Array.isArray(payload.contents)) return latestMessagesFromChat(payload.contents, truncated, `${keyPrefix}-contents`)
    if (Array.isArray(payload.choices)) {
      const messages: AuditConversationMessage[] = []
      payload.choices.forEach((choice, index) => {
        if (!choice || typeof choice !== 'object') return
        const choiceObj = choice as Record<string, unknown>
        const message = objectField(choiceObj, 'message')
        const delta = objectField(choiceObj, 'delta')
        if (message && typeof message === 'object') {
          messages.push(...messagesFromChatMessage(message as Record<string, unknown>, truncated, `${keyPrefix}-choice-${index}-message`))
        }
        if (delta && typeof delta === 'object') {
          messages.push(...messagesFromChatMessage({ ...(delta as Record<string, unknown>), role: 'assistant' }, truncated, `${keyPrefix}-choice-${index}-delta`))
        }
      })
      if (messages.length > 0) return messages
    }
    if (Array.isArray(payload.output)) return messagesFromResponsesInput(payload.output, truncated, `${keyPrefix}-output`)
  }

  const rawText = contentToText(value)
  if (fallbackKind === 'user' && isInternalAuditPrompt(rawText)) return []
  const text = fallbackKind === 'user' && !looksLikeToolOutput(rawText) ? extractUserInputText(value) : rawText
  const message = makeConversationMessage(fallbackKind, text, truncated, `${keyPrefix}-${fallbackKind}`)
  return message ? [message] : []
}

function isInternalAuditPrompt(value: unknown) {
  const text = contentToText(value).trim()
  if (!text) return false
  if (/^<session>\s*[\s\S]*?\s*<\/session>$/.test(text)) return true
  if (text.startsWith('The user stepped away and is coming back. Recap in under 40 words')) return true
  return false
}

function looksLikeToolOutput(value: string) {
  const text = value.trim()
  if (!text) return false
  if (text.startsWith('<tool_use_id>')) return true
  if (text.startsWith('<local-command-stdout>')) return true
  if (text.startsWith('<command-message>')) return true
  if (/^total\s+\d+\n[-dl]/m.test(text)) return true
  if ((text.match(/^\d+\t/gm) || []).length >= 3) return true
  if (/^(stdout|stderr|exit code|command):/i.test(text)) return true
  if (/^[-dlrwx]{10}\s+\d+\s+/m.test(text)) return true
  return false
}

function normalizeHistoricalToolMessages(turns: AuditTurn[]) {
  turns.forEach((turn, index) => {
    const previous = turns[index - 1]
    let hasReclassifiedToolResult = false
    turn.messages = turn.messages.map((message) => {
      if (message.kind !== 'user') return message
      const previousHasModelOutput = previous?.messages.some((item) => item.kind === 'assistant' && item.text.trim()) ?? false
      if (looksLikeToolOutput(message.text) || (previous && !previousHasModelOutput && looksLikeLegacyToolOutput(message.text))) {
        hasReclassifiedToolResult = true
        return { ...message, kind: 'tool_result' as const }
      }
      return message
    })
    if (hasReclassifiedToolResult && previous && !previous.messages.some((message) => message.kind === 'tool_call')) {
      previous.messages.push({
        key: `${previous.key}-inferred-tool-call`,
        kind: 'tool_call',
        text: t('admin.audit.inferredToolCall'),
        truncated: false,
      })
    }
  })
  return turns
}

function looksLikeLegacyToolOutput(value: string) {
  const text = value.trim()
  if (!text.includes('\n')) return false
  const lines = text.split('\n').filter((line) => line.trim() !== '')
  if (lines.length < 3) return false
  if ((lines.filter((line) => /^\s*\d+[.)]\s+/.test(line)).length >= 3)) return true
  if ((lines.filter((line) => /^\s*(\w+:)?\/[\w./-]+/.test(line)).length >= 3)) return true
  if ((lines.filter((line) => /^[A-Za-z_][\w.-]+\s+\d+/.test(line)).length >= 3)) return true
  return false
}

function parseAuditContentItems(value: string): AuditContentItem[] | null {
  if (!value) return null
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed) && parsed.every((item) => item && typeof item === 'object' && 'content' in item)) {
      return parsed as AuditContentItem[]
    }
  } catch {
    return null
  }
  return null
}

function buildAuditTurns(item: AuditLog): AuditTurn[] {
  const requests = parseAuditContentItems(item.request_body)
  const responses = parseAuditContentItems(item.response_body)
  if (!requests || !responses) {
    if (isInternalAuditPrompt(item.request_body)) return []
    return normalizeHistoricalToolMessages([{
      key: item.request_id || String(item.id),
      title: `#1 ${formatTime(item.created_at)} ${item.model || '-'}`,
      messages: [
        ...conversationMessagesFromPayload(item.request_body, 'user', item.request_truncated, `${item.request_id || item.id}-request`),
        ...conversationMessagesFromPayload(prettifyJSON(item.response_body), 'assistant', item.response_truncated, `${item.request_id || item.id}-response`),
      ],
    }])
  }
  const count = Math.max(requests.length, responses.length)
  const turns = Array.from({ length: count }, (_, index) => {
    const request = requests[index]
    const response = responses[index]
    const createdAt = request?.created_at || response?.created_at || item.created_at
    const model = request?.model || response?.model || item.model || '-'
    const requestID = request?.request_id || response?.request_id || item.request_id || String(index)
    const internalRequest = isInternalAuditPrompt(request?.content)
    return {
      key: `${requestID}-${index}`,
      title: `#${index + 1} ${formatTime(createdAt || '')} ${model}`,
      messages: internalRequest
        ? []
        : [
            ...conversationMessagesFromPayload(request?.content, 'user', Boolean(request?.truncated), `${requestID}-${index}-request`),
            ...conversationMessagesFromPayload(response?.content, 'assistant', Boolean(response?.truncated), `${requestID}-${index}-response`),
          ],
    }
  }).filter((turn) => turn.messages.length > 0)
  return normalizeHistoricalToolMessages(turns)
}

function mapAuditLog(item: AuditLog): AuditRow {
  return {
    id: String(item.id),
    time: formatTime(item.updated_at || item.created_at),
    user: item.user_email || '-',
    apiKey: item.api_key_name || (item.api_key_id ? `#${item.api_key_id}` : '-'),
    account: item.group_name || (item.group_id ? `#${item.group_id}` : '-'),
    platform: item.platform || '-',
    model: item.model || '-',
    endpoint: item.endpoint || '-',
    sessionId: item.session_id || item.request_id || '-',
    requestCount: item.request_count || 1,
    statusCode: item.status_code,
    requestId: item.request_id || '-',
    latency: `${item.duration_ms} ms`,
    ipAddress: item.ip_address || '-',
    requestTruncated: item.request_truncated,
    responseTruncated: item.response_truncated,
    turns: buildAuditTurns(item),
  }
}

async function loadRows() {
  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await auditAPI.list({
      page: pagination.page,
      page_size: pagination.pageSize,
      search: filters.keyword.trim() || undefined,
      platform: filters.platform === 'all' ? undefined : filters.platform,
      model: filters.model.trim() || undefined,
      endpoint: filters.endpoint.trim() || undefined,
    }, { signal: controller.signal })
    rows.value = result.items.map(mapAuditLog)
    pagination.total = result.total
    pagination.page = result.page
    pagination.pageSize = result.page_size
    pagination.pages = result.pages
    selectedRow.value = rows.value.find((row) => row.id === selectedRow.value?.id) || rows.value[0] || null
  } catch (error: any) {
    if (error?.code === 'ERR_CANCELED') return
    errorMessage.value = error?.response?.data?.message || error?.message || t('admin.audit.loadFailed')
  } finally {
    if (activeController === controller) {
      activeController = null
      loading.value = false
    }
  }
}

function goPage(page: number) {
  pagination.page = Math.min(Math.max(1, page), pagination.pages)
  loadRows()
}

function scrollConversationToBottom() {
  nextTick(() => {
    const viewport = conversationViewport.value
    if (!viewport) return
    viewport.scrollTop = viewport.scrollHeight
  })
}

async function loadOlderConversationTurns() {
  if (loadingOlderTurns.value || allConversationTurnsLoaded.value) return
  const viewport = conversationViewport.value
  const previousScrollHeight = viewport?.scrollHeight ?? 0
  const previousScrollTop = viewport?.scrollTop ?? 0

  loadingOlderTurns.value = true
  visibleTurnCount.value = Math.min(
    selectedTurns.value.length,
    visibleTurnCount.value + CONVERSATION_TURN_PAGE_SIZE,
  )
  await nextTick()
  if (viewport) {
    viewport.scrollTop = viewport.scrollHeight - previousScrollHeight + previousScrollTop
  }
  loadingOlderTurns.value = false
}

function handleConversationScroll() {
  const viewport = conversationViewport.value
  if (!viewport || viewport.scrollTop > 24) return
  loadOlderConversationTurns()
}

let filterTimer: ReturnType<typeof setTimeout> | undefined
watch(filters, () => {
  if (filterTimer) clearTimeout(filterTimer)
  filterTimer = setTimeout(() => {
    pagination.page = 1
    loadRows()
  }, 300)
}, { deep: true })

watch(() => selectedRow.value?.id, () => {
  visibleTurnCount.value = CONVERSATION_TURN_PAGE_SIZE
  scrollConversationToBottom()
})

watch(() => selectedTurns.value.length, () => {
  visibleTurnCount.value = Math.min(
    Math.max(CONVERSATION_TURN_PAGE_SIZE, visibleTurnCount.value),
    Math.max(CONVERSATION_TURN_PAGE_SIZE, selectedTurns.value.length),
  )
  if (!loadingOlderTurns.value) {
    scrollConversationToBottom()
  }
})

onMounted(loadRows)

onBeforeUnmount(() => {
  activeController?.abort()
  if (filterTimer) clearTimeout(filterTimer)
})
</script>
