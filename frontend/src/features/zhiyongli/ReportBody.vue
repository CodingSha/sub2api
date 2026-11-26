<template>
  <AssessmentReader v-if="report.period_type === 'day' && report.daily_review && report.items?.length" :report="report" :period-label="report.period_start" :source-labels="report.daily_review.source_labels" :source-sessions="report.daily_review.source_sessions" :conversations="report.daily_review.conversations" />
  <AssessmentReader v-else-if="report.weekly_review && report.items?.length" :report="report" :reveal-score="revealScore" :period-label="`${report.weekly_review.sources[0].period_start} — ${report.weekly_review.sources.at(-1)?.period_start}`" :source-labels="report.weekly_review.source_labels" :source-sessions="report.weekly_review.source_sessions" :conversations="report.weekly_review.conversations" />
  <section v-else-if="reviewPending(report)" aria-label="评审状态">
    <h2 class="score-title text-gray-900 dark:text-gray-100">智用力评分</h2>
    <p class="mt-3 text-sm text-gray-600 dark:text-gray-300">评审待完成</p>
    <WorkdayRange v-if="report.workday_scope" :scope="report.workday_scope" class="mt-5" />
  </section>
  <section v-else>
    <div class="score-hero border-b border-gray-200 dark:border-dark-700">
      <div class="score-summary">
        <div class="flex flex-wrap items-center gap-3">
          <h2 class="score-title text-gray-900 dark:text-gray-100">智用力评分</h2>
          <span class="badge badge-gray">{{ periodLabel ?? (report.period_type === 'day' ? '日评' : report.period_type === 'week' ? '周评' : '月评') }}</span>
        </div>
        <div v-if="report.period_type !== 'day' || report.score_status !== 'scored'" class="my-3 flex flex-wrap gap-2" :class="displayedScore === null ? 'items-center' : 'items-baseline'">
          <ScoreReveal v-if="displayedScore !== null" :key="`${report.id}:${report.revision}`" class="score-number text-gray-900 dark:text-gray-100" :score="displayedScore" :exact="displayedExact" :animate="revealScore === true" :identity="`${report.user_id}:${report.id}:${report.revision}`" />
          <span v-else class="text-sm text-gray-600 dark:text-gray-300">{{ report.period_type === 'day' && report.score_status === 'observation_only' ? '仅作维度观察' : scoreStatusLabel(report) }}</span>
          <span v-if="displayedScore !== null" class="text-sm text-gray-400">/ 100</span>
          <span v-if="report.company_rank" class="ml-4 text-sm text-gray-500 dark:text-gray-400">公司排名 <strong class="text-lg text-primary-700 dark:text-primary-400">{{ report.company_rank.tied ? '并列 ' : '' }}{{ report.company_rank.position }}</strong> / {{ report.company_rank.total }}</span>
          <span v-if="report.rating" class="badge badge-success">{{ report.rating }}</span>
          <span v-else-if="report.period_type === 'month' && displayedScore !== null" class="text-sm text-amber-700 dark:text-amber-400">评级待定</span>
          <HelpTooltip v-if="report.period_type !== 'day' && (report.score_explanation || report.score_reason || hasDimensionObservations)" class="self-center" width-class="w-80">
            <template #trigger="{ open, tooltipId }">
              <button type="button" class="inline-flex h-8 w-8 items-center justify-center rounded hover:bg-gray-500/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500" :class="scoreNeedsAttention(report) ? 'text-orange-600 dark:text-orange-400' : 'text-gray-500 dark:text-gray-400'" aria-label="查看评分说明" :aria-describedby="open ? tooltipId : undefined">
                <Icon :name="scoreNeedsAttention(report) ? 'exclamationTriangle' : 'infoCircle'" size="sm" aria-hidden="true" />
              </button>
            </template>
            <ScoreExplanation :report="report" />
          </HelpTooltip>
        </div>
        <p v-if="report.eligibility" class="mt-3 text-sm text-gray-500 dark:text-gray-400">{{ report.eligibility.task_count }} 个任务 · 使用 {{ report.eligibility.active_days }} 天<span v-if="report.period_type === 'month'"> · <template v-if="['1.4', '1.5', '1.6'].includes(report.policy_versions.rules)">{{ report.eligibility.valid_weeks ?? 0 }} 个有效周评</template><template v-else>覆盖 {{ report.eligibility.active_weeks }} 个自然周</template></span></p>
        <div class="mt-6">
          <h3 v-if="hasDimensionObservations" class="mb-2 text-sm font-medium text-gray-600 dark:text-gray-400">维度观察分</h3>
          <div class="grid grid-cols-2 gap-x-6 text-sm">
            <div v-for="dimension in report.dimensions" :key="dimension.code" class="dimension-summary">
              <div class="text-gray-600 dark:text-gray-400">{{ dimension.name }}</div>
              <div class="text-xl font-semibold tabular-nums text-gray-900 dark:text-gray-100">{{ dimension.score === null ? '—' : dimension.score }}</div>
            </div>
          </div>
        </div>
        <p v-if="report.published_at" class="mt-5 text-xs text-gray-500 dark:text-gray-400">发布于 {{ formatTime(report.published_at) }}</p>
      </div>
      <CapabilityRadar v-if="report.dimensions.length" :dimensions="report.dimensions" />
    </div>
    <slot name="summary" />
    <section v-if="report.items?.length" class="pt-8" aria-label="分项评分与标准">
      <h2 class="mb-4 text-lg font-semibold">分项评分与标准</h2>
      <div class="dimension-tabs flex border-b border-gray-200 dark:border-dark-700" role="tablist" aria-label="评分维度">
        <button v-for="dimension in chartDimensions" :id="`${panelId}-${dimension.code}-tab`" :key="dimension.code" type="button" role="tab" class="dimension-tab flex-1 border-b-2 px-3 py-3 text-center text-sm focus-visible:outline-primary-500" :class="selectedDimension === dimension.code ? 'border-primary-500 font-semibold text-primary-700 dark:text-primary-400' : 'border-transparent text-gray-500 dark:text-gray-400'" :aria-selected="selectedDimension === dimension.code" :aria-controls="`${panelId}-panel`" :tabindex="selectedDimension === dimension.code ? 0 : -1" @click="selectedDimension = dimension.code" @keydown="switchDimension($event, dimension.code)">
          <span>{{ dimension.name }}</span><span class="ml-3 tabular-nums">{{ dimension.score ?? '—' }}</span>
        </button>
      </div>
      <div :id="`${panelId}-panel`" :key="`${report.id}:${report.revision}:${selectedDimension}`" role="tabpanel" :aria-labelledby="`${panelId}-${selectedDimension}-tab`" tabindex="0">
      <details v-for="item in selectedItems" :key="item.item_code" :open="item.item_code === firstItem" class="item-row border-b border-gray-200 dark:border-dark-700">
        <summary class="flex cursor-pointer items-center gap-3 py-4 focus-visible:outline-primary-500">
          <span class="item-dimension text-xs text-gray-500 dark:text-gray-400">{{ dimensionNames[item.item_code[0]] }}</span>
          <span class="flex-1 font-medium">{{ item.name }}</span>
          <span v-if="item.score !== null" class="font-semibold tabular-nums">{{ item.score }}<span class="ml-1 text-xs font-normal text-gray-400">分</span></span>
          <span v-else class="text-sm text-gray-500">{{ stateText[item.state] }}</span>
          <Icon name="chevronDown" size="sm" aria-hidden="true" class="expand-arrow shrink-0 text-gray-500 dark:text-gray-400" />
        </summary>
        <div class="item-content pb-6 pr-8 text-sm leading-7 text-gray-700 dark:text-gray-300">
          <p v-if="item.state === 'related'" class="mb-5 text-gray-500 dark:text-gray-400">本项仅作关联说明，不单独计分。</p>
          <div v-if="item.observations.length" class="mb-5">
            <h3 class="explanation-label">本期表现</h3>
            <ul class="explanation-list"><li v-for="observation in item.observations" :key="observation"><ReportProse :text="observation" /></li></ul>
          </div>
          <div v-if="report.period_type === 'week' && item.explanations.length === 1" class="mb-5">
            <h3 class="explanation-label">{{ item.score === null ? '为什么不单独计分' : '为什么得到这个分数' }}</h3>
            <ReportProse v-for="line in item.explanations[0].analysis" :key="line" :text="line" />
            <template v-if="item.explanations[0].basis.length">
              <h3 class="explanation-label mt-4">距离下一档还差什么</h3>
              <ReportProse v-for="line in item.explanations[0].basis" :key="line" :text="line" />
            </template>
          </div>
          <details v-else-if="item.explanations.length" class="explanation-detail" :open="!item.observations.length">
            <summary class="detail-toggle">{{ report.period_type === 'month' ? '各周评分说明' : '任务综合分析' }}</summary>
            <div v-for="(section,index) in item.explanations" :key="index" class="mt-4">
              <h3 v-if="section.date" class="explanation-label">{{ section.date }} 起</h3>
              <ul class="explanation-list"><li v-for="line in section.analysis" :key="line"><ReportProse :text="line" /></li></ul>
              <template v-if="section.basis.length && (report.period_type === 'month' || item.explanations.length > 1)"><h4 class="explanation-label mt-3">分档依据与尚未达到更高档位的原因</h4><ul class="explanation-list"><li v-for="line in section.basis" :key="line"><ReportProse :text="line" /></li></ul></template>
            </div>
          </details>
          <details v-if="item.criterion" class="explanation-detail">
            <summary class="detail-toggle">评分标准</summary>
            <p class="mt-3">{{ displayText(item.criterion) }}</p>
          </details>
          <details v-if="report.period_type === 'month' && item.period_states" class="mt-3">
            <summary class="cursor-pointer text-primary-700 dark:text-primary-400">月分计算依据</summary>
            <table class="mt-2 w-full text-left text-xs">
              <thead><tr><th class="py-2">起始日期</th><th>分项分数 / 状态</th><th>覆盖天数</th></tr></thead>
              <tbody><tr v-for="(state,start) in item.period_states" :key="start" class="border-t border-gray-200 dark:border-dark-700"><td class="py-2">{{ start }}</td><td>{{ componentFor(item,String(start))?.exact ?? stateText[state] }}</td><td>{{ componentDays(item,String(start)) }}</td></tr></tbody>
            </table>
          </details>
          <template v-if="reviewDetails"><p v-for="limit in item.limitations" :key="limit" class="mt-2 text-amber-700 dark:text-amber-400">{{ displayText(limit) }}</p></template>
          <div v-if="item.tasks.length" class="task-evidence mt-6">
            <h3 class="explanation-label">相关任务 <span class="ml-1 font-normal text-gray-500">{{ item.tasks.length }} 个</span></h3>
            <details v-for="task in item.tasks" :key="task.title" class="task-record border-b border-gray-200 dark:border-dark-700">
              <summary class="flex cursor-pointer items-start gap-3 py-3 font-medium focus-visible:outline-primary-500">
                <span class="min-w-0 flex-1">{{ task.title }}</span><span class="shrink-0 text-xs font-normal text-gray-500 dark:text-gray-400">{{ task.records.length }} 条记录</span><Icon name="chevronDown" size="sm" aria-hidden="true" class="expand-arrow shrink-0 text-gray-500 dark:text-gray-400" />
              </summary>
              <div v-for="evidence in visibleEvidence(item.item_code,task.title,task.records)" :key="evidence.id" class="mb-4">
                <div class="mb-2 flex flex-wrap gap-x-3 text-xs text-gray-500 dark:text-gray-400"><span>{{ formatTime(evidence.occurred_at) }}</span><span>{{ roleText[evidence.role] }} · {{ originText[evidence.origin] }}</span></div>
                <blockquote class="evidence-quote whitespace-pre-wrap border-l-2 border-primary-400 pl-4">{{ evidence.quote }}</blockquote>
                <details v-if="sourceContext(evidence) !== evidence.quote" class="mt-2">
                  <summary class="detail-toggle">查看前后文</summary>
                  <p class="mt-3 whitespace-pre-wrap break-words text-gray-600 dark:text-gray-400">{{ sourceContext(evidence) }}</p>
                </details>
                <template v-if="reviewDetails"><p v-for="limit in evidence.limitations" :key="limit" class="mt-1 text-xs leading-5 text-amber-700 dark:text-amber-400">{{ limit }}</p></template>
              </div>
              <button v-if="task.records.length > 2" class="mb-4 text-primary-700 dark:text-primary-400" :aria-expanded="expandedEvidence.has(evidenceKey(item.item_code,task.title))" @click="toggleEvidence(evidenceKey(item.item_code,task.title))">{{ expandedEvidence.has(evidenceKey(item.item_code,task.title)) ? '收起记录' : `查看其余 ${task.records.length-2} 条记录` }}</button>
            </details>
          </div>
        </div>
      </details>
      </div>
    </section>
    <TaskReviewSection v-if="hasTaskReview" :key="`${report.id}-${report.revision}-${reviewDetails}`" :report="report" :admin="reviewDetails" />
    <EvidenceCases v-else :report="report" :admin="reviewDetails" />
    <details v-if="reviewDetails && report.limitations?.length" class="mt-5 text-sm">
      <summary class="detail-toggle">资料范围与限制</summary>
      <ul class="explanation-list mt-3 text-gray-600 dark:text-gray-400"><li v-for="limit in report.limitations" :key="limit">{{ displayText(limit) }}</li></ul>
    </details>
    <details v-if="reviewDetails && report.assessment?.issues.length" class="mt-5 text-sm">
      <summary class="cursor-pointer text-amber-700 dark:text-amber-400">资料限制与处置 · {{ report.assessment.issues.length }} 条</summary>
      <div v-for="issue in report.assessment.issues" :key="issue.id" class="border-b border-gray-200 py-3 dark:border-dark-700">
        <p><span v-if="issue.status === 'blocking'" class="mr-2 font-medium text-amber-700 dark:text-amber-400">月级待定</span>{{ displayText(issue.description) }}</p>
        <p class="mt-1 text-gray-500 dark:text-gray-400">{{ displayText(issue.reason) }}</p>
      </div>
    </details>
    <WorkdayRange v-if="report.workday_scope" :scope="report.workday_scope" class="mt-5" />
    <details v-if="report.items?.length" class="mt-5 text-sm text-gray-600 dark:text-gray-400">
      <summary class="cursor-pointer">计分范围</summary>
      <p v-for="d in report.dimensions" :key="d.code" class="mt-2">{{ d.name }}：已评分 {{ numericItems(d.code).length }} / 3 项</p>
      <template v-if="reviewDetails && report.eligibility">
        <p v-for="d in report.eligibility.dimension_coverage" :key="`coverage-${d.code}`" class="mt-2">{{ dimensionNames[d.code] }}本人依据：{{ d.events }} 处来源，{{ d.tasks }} 个任务，{{ d.days }} 个日期<span v-if="report.period_type === 'month'">，{{ d.weeks }} 个自然周</span></p>
        <p class="mt-2">任务和使用日期用于检查观察范围，不参与加减分。</p>
      </template>
      <p v-if="reviewDetails" class="mt-2">使用效率依据：{{ efficiencyEventCount }} 条来源事件。依据数量不作为加减分条件。</p>
      <p v-if="reviewDetails && efficiencySensitivity !== null" class="mt-2">固定其他项，在有效分值范围内，使用效率按子项等权时，一个已评分子项增加10分，总分增加 {{ efficiencySensitivity }} 分；必要缺口按缺口优先规则重新计算。</p>
    </details>
    <details class="mt-5 text-xs text-gray-500 dark:text-gray-400">
      <summary class="cursor-pointer">评分版本</summary>
      <p class="mt-2">规则 {{ report.policy_versions.rules }} · 报告修订 {{ report.revision }}</p>
    </details>
  </section>
</template>

<script setup lang="ts">
import CapabilityRadar from './CapabilityRadar.vue'
import AssessmentReader from './AssessmentReader.vue'
import { computed, ref, useId, watch } from 'vue'
import HelpTooltip from '@/components/common/HelpTooltip.vue'
import Icon from '@/components/icons/Icon.vue'
import WorkdayRange from './WorkdayRange.vue'
import ScoreExplanation from './ScoreExplanation.vue'
import EvidenceCases from './EvidenceCases.vue'
import ReportProse from './ReportProse.vue'
import TaskReviewSection from './TaskReviewSection.vue'
import ScoreReveal from './ScoreReveal.vue'
import { stateText, type Report, type Item, type Evidence } from './api'
import { checkedWeekJudgment, reviewPending, dimensionNames, evidenceForDisplay, explanation, observationsForDisplay, reportText, scoreNeedsAttention, scoreStatusLabel, sourceContext, taskEvidence } from './reportPresentation'
const props = defineProps<{ report: Report; reviewDetails?: boolean; revealScore?: boolean; periodLabel?: string }>()
const hasTaskReview = computed(() => {
  const value = props.report.task_review
  return props.report.period_type === 'week' && !!value && (value.version === 'task-evidence-20261106.1' && ['case_checked', 'week_checked', 'generated'].includes(value.status) || value.version === 'source-evidence-20261116.1' && value.status === 'source_linked')
})
const displayedScore = computed(() => props.report.period_type === 'day' ? null : props.report.score)
const displayedExact = computed(() => props.report.period_type === 'day' ? null : props.report.exact)
const hasDimensionObservations = computed(() => props.report.score === null && props.report.dimensions.some(d => d.score !== null))
const expandedEvidence = ref(new Set<string>())
const displayText = computed(() => reportText(props.report))
const presentedItems = computed(() => (props.report.items ?? []).map(item => {
  const records = props.reviewDetails ? item.evidence ?? [] : evidenceForDisplay(item)
  const judgment = checkedWeekJudgment(props.report, item.item_code)
  const explanations = judgment ? explanation({ ...item, reason: judgment.reason }, displayText.value) : explanation(item, displayText.value)
  const observations = props.reviewDetails ? [...new Set(records.map(e => e.supports).filter(Boolean))].map(displayText.value) : observationsForDisplay(item, displayText.value)
  return {
    ...item,
    observations: observations.filter(text => !explanations.some(section => section.analysis.join('') === text)),
    explanations,
    tasks: taskEvidence({ ...item, evidence: records })
  }
}))
const panelId = useId()
const selectedDimension = ref('P')
watch(() => [props.report.id, props.report.revision], () => {
  selectedDimension.value = props.report.dimensions[0]?.code ?? 'P'
}, { immediate: true })
const selectedItems = computed(() => presentedItems.value.filter(item => item.item_code.startsWith(selectedDimension.value)))
const firstItem = computed(() => selectedItems.value.find(item => item.score !== null)?.item_code ?? selectedItems.value[0]?.item_code)
function switchDimension(event: KeyboardEvent, code: string) {
  const codes = chartDimensions.value.map(dimension => dimension.code)
  const index = codes.indexOf(code)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? codes.length - 1 : event.key === 'ArrowRight' ? (index + 1) % codes.length : event.key === 'ArrowLeft' ? (index + codes.length - 1) % codes.length : -1
  if (next < 0) return
  event.preventDefault()
  selectedDimension.value = codes[next]
  document.getElementById(`${panelId}-${codes[next]}-tab`)?.focus()
}
const evidenceKey = (code: string, title: string) => JSON.stringify([code, title])
const visibleEvidence = (code: string, title: string, records: Evidence[]) => expandedEvidence.value.has(evidenceKey(code,title)) ? records : records.slice(0,2)
const componentFor = (item:Item,start:string) => item.components?.find(c=>c.period_start===start)
const componentDays = (item:Item,start:string) => {const c=componentFor(item,start);return c ? (Date.parse(c.period_end)-Date.parse(c.period_start))/86400000 : '不计入分项分母'}
function toggleEvidence(code: string) { if(expandedEvidence.value.has(code)) expandedEvidence.value.delete(code);else expandedEvidence.value.add(code) }
const numericItems = (code: string) => props.report.items?.filter(item => item.item_code.startsWith(code) && item.score !== null) ?? []
const efficiencyEventCount = computed(() => new Set(numericItems('E').flatMap(item => item.evidence?.map(e => e.source_ref) ?? [])).size)
const efficiencySensitivity = computed(() => {
  const count = numericItems('E').length
  return count && props.report.score !== null ? (1.5 / count).toFixed(2).replace(/0$/, '') : null
})
const chartDimensions = computed(() => ['P','M','J','E'].map(code => props.report.dimensions.find(d => d.code === code)!).filter(Boolean))
const roleText = { user: '用户消息', assistant: 'AI 回复', tool: '工具记录', context: '来源上下文' }
const originText: Record<string,string> = { direct_expression:'本人表达', explicit_adoption:'明确采纳', quoted_or_forwarded:'引用或转发', uncertain:'归属未明', context:'背景材料' }
const formatTime = (s: string) => new Date(s).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
</script>

<style scoped>
.score-hero{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:24px;padding-bottom:28px;align-items:center}
.score-summary{min-width:0;padding:12px 0}.score-number{font-size:76px;line-height:1;font-weight:650;letter-spacing:-4px;font-variant-numeric:tabular-nums}
.score-title{font-size:24px;line-height:32px;font-weight:600}
.dimension-summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid rgb(148 163 184 / .24)}

.item-dimension{width:88px;flex-shrink:0}.item-content{padding-left:100px;max-width:880px;overflow-wrap:anywhere}.explanation-label{font-size:12px;line-height:20px;font-weight:600;color:#64748b;margin-bottom:8px}.explanation-list{list-style:disc;padding-left:18px}.explanation-list li+li{margin-top:8px}.explanation-detail{margin-top:12px}.detail-toggle{cursor:pointer;color:#0f766e;font-size:13px;line-height:24px}.task-record:last-child{border-bottom:0}.evidence-quote{overflow-wrap:anywhere}
:global(.dark .explanation-label){color:#94a3b8}:global(.dark .detail-toggle){color:#5eead4}
.item-row>summary,.task-record>summary{list-style:none}.item-row>summary::-webkit-details-marker,.task-record>summary::-webkit-details-marker{display:none}.expand-arrow{transition:transform 160ms}.item-row[open]>summary .expand-arrow,.task-record[open]>summary .expand-arrow{transform:rotate(180deg)}
@media(prefers-reduced-motion:reduce){.expand-arrow{transition:none}}
</style>
