<template>
  <article class="task-article text-gray-700 dark:text-gray-300" :aria-label="task.title">
    <header class="article-heading">
      <div class="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400"><span>任务案例</span><span>{{ task.record_count }} 条记录</span></div>
      <h3 class="text-xl font-semibold leading-relaxed text-gray-900 dark:text-gray-100">{{ task.title }}</h3>
      <ReportProse class="task-goal mt-3" :text="task.goal" />
    </header>

    <section class="article-section process-surface" aria-label="任务过程与评判">
      <h4 class="article-label">任务过程与评判</h4>
      <div v-for="group in claimGroups" :key="group.start" class="claim-group">
        <div class="claim-heading mb-2 flex flex-wrap items-center gap-x-3 gap-y-2">
          <span v-for="reference in group.references" :key="reference.code" class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ dimensionNames[reference.code] }} · {{ reference.names.join('、') }}</span>
          <span v-if="!group.references.length" class="text-sm font-semibold text-gray-700 dark:text-gray-300">任务情况</span>
        </div>
        <div v-for="{claim,index} in group.claims" :key="index" class="claim-section">
        <div v-if="claim.assessment" class="claim-assessment mb-3" :class="`assessment-${claim.assessment.kind}`">
          <span class="claim-verdict" :class="verdictTone[claim.assessment.kind]">{{ verdictText[claim.assessment.kind] }}</span>
          <ReportProse class="claim-impact font-medium" :class="verdictTone[claim.assessment.kind]" :text="claim.assessment.reason" />
        </div>
        <ReportProse v-if="claim.text.trim() !== claim.assessment?.reason.trim()" class="analysis-paragraph" :text="claim.text" />
        <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
          <button v-if="claim.record_ids.length" type="button" class="source-toggle" :aria-expanded="activeClaim === index" :aria-controls="`${articleId}-claim-${index}`" @click="toggleClaim(index)">{{ activeClaim === index ? '收起原文' : `对应记录 · ${claim.record_ids.length} 条` }}</button>
        </div>
        <div v-if="activeClaim === index" :id="`${articleId}-claim-${index}`" class="source-panel mt-4 bg-gray-50 dark:bg-dark-800" :aria-busy="loading">
          <TaskRecordList :records="claimRecords(claim)" />
          <p v-if="loading" role="status" class="py-3 text-sm text-gray-500">正在读取对应记录…</p>
          <div v-else-if="error && !conversationOpen" class="py-3 text-sm"><p role="alert" class="text-red-700 dark:text-red-400">{{ error }}</p><button type="button" class="source-toggle mt-2" @click="retry">重试</button></div>
          <p v-else-if="missingClaimRecords(claim)" class="py-3 text-sm text-amber-700 dark:text-amber-400">部分对应记录未返回，当前无法完整展示。</p>
          <details v-if="claim.limitations?.length" class="py-3 text-xs leading-6 text-gray-500 dark:text-gray-400"><summary class="cursor-pointer">这段判断的适用范围</summary><p v-for="limit in claim.limitations" :key="limit" class="mt-2">{{ limit }}</p></details>
        </div>
        </div>
      </div>
    </section>

    <section class="article-section outcome-surface" aria-label="任务结果">
      <h4 class="article-label">任务结果</h4>
      <ReportProse class="analysis-paragraph" :text="task.outcome" />
    </section>

    <section v-if="relatedItems.length" class="article-section" aria-label="与本周评分的对应">
      <h4 class="article-label">与本周评分的对应</h4>
      <details v-for="item in relatedItems" :key="item.item_code" class="score-correspondence border-b border-gray-200 dark:border-dark-700">
        <summary class="flex cursor-pointer items-center justify-between gap-4 py-3 text-sm">
          <span><span class="mr-3 text-gray-500 dark:text-gray-400">{{ dimensionNames[item.item_code[0]] }}</span><span class="font-medium">{{ item.name }}</span></span>
          <span class="shrink-0 font-semibold tabular-nums" :class="scoreTone(item.score)">{{ item.score ?? stateText[item.state] }}<span v-if="item.score !== null" class="ml-2 text-xs font-normal text-gray-500">本周分</span><span class="ml-3 text-gray-400" aria-hidden="true">⌄</span></span>
        </summary>
        <div class="pb-4 text-sm leading-6">
          <div v-if="item.criterion" class="judgment-block standard-surface"><h5 class="mb-1 font-medium">评分标准</h5><ReportProse :text="displayText(item.criterion)" /></div>
          <div v-if="weekJudgment(item.item_code)" class="judgment-block reasoning-surface mt-3"><h5 class="mb-1 font-medium">{{ item.score === null ? '未评分原因' : '评分原因' }}</h5><p>{{ displayText(weekJudgment(item.item_code)!.reason) }}</p><p v-for="limit in weekJudgment(item.item_code)!.limitations" :key="limit" class="mt-2 text-gray-500 dark:text-gray-400">{{ limit }}</p></div>
          <template v-else>
            <div v-for="(section,index) in explanation(item, displayText)" :key="index" class="mt-3 space-y-3">
              <div class="judgment-block reasoning-surface">
              <h5 class="mb-1 font-medium">{{ item.score === null ? '未评分原因' : '评分原因' }}</h5>
              <ReportProse :text="section.analysis.join('')" />
              </div>
              <div v-if="section.basis.length" class="judgment-block boundary-surface"><h5 class="mb-1 font-medium">分档依据与未达更高档的原因</h5><ReportProse :text="section.basis.join('')" /></div>
            </div>
          </template>
        </div>
      </details>
    </section>

    <details class="article-section conversation" :open="conversationOpen" @toggle="toggleConversation">
      <summary class="flex cursor-pointer items-center justify-between gap-4 py-3 font-medium text-gray-900 dark:text-gray-100"><span>查看任务对话</span><span class="text-xs font-normal text-gray-500">已保存的 {{ task.record_count }} 条记录</span></summary>
      <p v-if="recordAvailability" class="my-3 text-sm leading-6 text-amber-700 dark:text-amber-400">{{ recordAvailability }}</p>
      <TaskRecordList v-if="conversationOpen" :records="records" />
      <p v-if="conversationOpen && loading" role="status" class="py-3 text-sm text-gray-500">正在读取任务记录…</p>
      <div v-else-if="conversationOpen && error" class="py-3 text-sm"><p role="alert" class="text-red-700 dark:text-red-400">{{ error }}</p><button type="button" class="source-toggle mt-2" @click="retry">重试</button></div>
      <div v-else-if="conversationOpen" class="flex items-center justify-between gap-4 py-4 text-sm text-gray-500 dark:text-gray-400"><span>已展示 {{ records.length }} / {{ total }} 条记录</span><button v-if="hasMore" type="button" class="source-toggle" @click="loadNext">继续加载</button></div>
      <details v-if="task.limitations?.length" class="border-t border-gray-200 py-4 text-sm leading-7 dark:border-dark-700"><summary class="cursor-pointer text-gray-500 dark:text-gray-400">记录范围与限制</summary><p v-for="limit in task.limitations" :key="limit" class="mt-2">{{ limit }}</p></details>
    </details>
  </article>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, useId } from 'vue'
import { getTaskCase, stateText, type Report, type TaskCase, type TaskClaim, type TaskRecord } from './api'
import { checkedWeekJudgment, dimensionNames, explanation, reportText, scoreTone } from './reportPresentation'
import TaskRecordList from './TaskRecordList.vue'
import ReportProse from './ReportProse.vue'

const props = defineProps<{ report: Report; task: TaskCase; admin?: boolean }>()
const articleId = useId()
const displayText = computed(() => reportText(props.report))
const verdictText = { support: '支持表现', shortfall: '表现不足', unresolved: '不计分原因' }
const verdictTone = { support: 'text-emerald-800 dark:text-emerald-300', shortfall: 'text-orange-800 dark:text-orange-300', unresolved: 'text-gray-600 dark:text-gray-400' }
const itemsFor = (codes: string[] | null) => (props.report.items ?? []).filter(item => codes?.includes(item.item_code))
const claimGroups = computed(() => {
  const groups: { key: string; start: number; references: {code: string; names: string[]}[]; claims: {claim: TaskClaim; index: number}[] }[] = []
  props.task.claims.forEach((claim, index) => {
    const items = itemsFor(claim.item_codes)
    const key = items.map(item => item.item_code).sort().join(',')
    const previous = groups[groups.length - 1]
    // 仅合并连续、关联子项相同的标题，保留原分析顺序及每段自己的引文和评价。
    if (previous && previous.key === key) previous.claims.push({ claim, index })
    else {
      const references: {code: string; names: string[]}[] = []
      for (const item of items) {
        const code = item.item_code[0]
        const reference = references.find(entry => entry.code === code)
        if (reference) reference.names.push(item.name)
        else references.push({ code, names: [item.name] })
      }
      groups.push({ key, start: index, references, claims: [{ claim, index }] })
    }
  })
  return groups
})
const relatedItems = computed(() => itemsFor([...new Set(props.task.claims.flatMap(claim => claim.item_codes ?? []))]))
const weekJudgment = (code: string) => {
  const judgment = checkedWeekJudgment(props.report, code)
  return judgment?.case_ids?.includes(props.task.id) ? judgment : undefined
}
const records = ref<TaskRecord[]>([])
const total = ref(props.task.record_count)
const page = ref(0)
const pageSize = ref(50)
const loading = ref(false)
const error = ref('')
const conversationOpen = ref(false)
const activeClaim = ref<number | null>(null)
const hasMore = computed(() => page.value === 0 || page.value * pageSize.value < total.value)
const claimRecords = (claim: TaskClaim) => records.value.filter(record => claim.record_ids.includes(record.id))
const missingClaimRecords = (claim: TaskClaim) => claimRecords(claim).length < claim.record_ids.length
const recordAvailability = computed(() => {
  const missingBody = records.value.filter(record => !record.text.trim()).length
  return missingBody
    ? `已读取记录中有 ${missingBody} 条未保存正文，具体位置已标注。缺失正文不用于评分。`
    : ''
})
let disposed = false
let pending: Promise<void> | null = null
onBeforeUnmount(() => { disposed = true })

async function loadNext() {
  if (pending) return pending
  if (!hasMore.value || disposed) return
  loading.value = true
  error.value = ''
  pending = (async () => {
    try {
      const data = await getTaskCase(Boolean(props.admin), props.report.id, props.task.id, page.value + 1)
      if (disposed) return
      records.value.push(...data.records)
      page.value = data.page
      pageSize.value = data.page_size
      total.value = data.total
    } catch (e) {
      if (!disposed) error.value = (e as { message: string }).message
    } finally {
      if (!disposed) loading.value = false
      pending = null
    }
  })()
  return pending
}
async function loadClaim(claim: TaskClaim) {
  // 依次复用分页缓存，直到关联原文可见；不截取首条代替整段对话。
  while (!disposed && missingClaimRecords(claim) && hasMore.value) {
    const previousPage = page.value
    await loadNext()
    if (error.value || page.value === previousPage) break
  }
}
async function toggleClaim(index: number) {
  activeClaim.value = activeClaim.value === index ? null : index
  if (activeClaim.value !== null) await loadClaim(props.task.claims[index])
}
function retry() {
  const claim = activeClaim.value !== null ? props.task.claims[activeClaim.value] : undefined
  if (claim && missingClaimRecords(claim)) void loadClaim(claim)
  else void loadNext()
}
async function toggleConversation(event: Event) {
  conversationOpen.value = (event.target as HTMLDetailsElement).open
  if (conversationOpen.value && page.value === 0) await loadNext()
}
</script>

<style scoped>
.task-article { max-width: 56rem; margin: 0 auto; }
.article-heading { padding-bottom: 1rem; border-bottom: 1px solid rgb(128 128 128 / .2); }
.task-goal { font-size: 15px; line-height: 1.65; }
.article-section { margin-top: 1.5rem; }
.article-label { margin-bottom: .75rem; font-size: 15px; font-weight: 600; color: inherit; }
.process-surface { padding: 20px; border-radius: 6px; background: rgb(236 242 245 / .8); }
.outcome-surface { padding: 16px 20px; border-radius: 6px; background: rgb(226 232 240 / .42); }
.claim-group + .claim-group { margin-top: 1.5rem; padding-top: 1.25rem; border-top: 1px solid rgb(128 128 128 / .14); }
.claim-section + .claim-section { margin-top: 1.25rem; }
.analysis-paragraph, .claim-impact { font-size: 15px; line-height: 1.7; white-space: pre-line; overflow-wrap: anywhere; text-wrap: pretty; }
.claim-verdict { display: inline-block; margin-bottom: 4px; font-size: 12px; font-weight: 600; }
.claim-assessment { padding: 10px 14px; border-radius: 4px; border-left: 2px solid rgb(128 128 128 / .3); background: rgb(226 232 240 / .45); }
.assessment-support { border-color: #7bac9b; background: rgb(225 240 233 / .8); }
.assessment-shortfall { border-color: #c69c71; background: rgb(244 235 222 / .8); }
.judgment-block { padding: 12px 16px; border-radius: 4px; }
.standard-surface { background: rgb(226 232 240 / .45); }
.reasoning-surface { background: rgb(225 240 233 / .7); }
.boundary-surface { background: rgb(237 233 224 / .7); }
:global(.dark) .process-surface { background: rgb(17 24 39 / .55); }
:global(.dark) .outcome-surface, :global(.dark) .standard-surface, :global(.dark) .claim-assessment { background: rgb(148 163 184 / .09); }
:global(.dark) .assessment-support, :global(.dark) .reasoning-surface { background: rgb(90 140 117 / .16); }
:global(.dark) .assessment-shortfall, :global(.dark) .boundary-surface { background: rgb(168 139 100 / .15); }
.source-toggle { color: var(--color-primary-700, #475e77); text-decoration: underline; text-underline-offset: 4px; cursor: pointer; padding: .25rem 0; }
:global(.dark) .source-toggle { color: #a7bacb; }
.source-panel { padding: 0 1.25rem; border-left: 2px solid rgb(128 128 128 / .3); border-radius: 0 4px 4px 0; }
.conversation { border-top: 1px solid rgb(128 128 128 / .2); }
summary:focus-visible, button:focus-visible { outline: 2px solid currentColor; outline-offset: 4px; }
.score-correspondence > summary { list-style: none; }
.score-correspondence > summary::-webkit-details-marker { display: none; }
</style>
