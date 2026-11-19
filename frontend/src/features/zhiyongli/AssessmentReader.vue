<template>
  <div class="assessment-reader">
    <section class="score-overview" aria-label="评分总览">
      <div class="total-score"><span>{{ periodLabel }}</span><strong>{{ report.score ?? '—' }}</strong><span class="inline-flex items-center gap-1">综合评分
        <HelpTooltip v-if="partialDailyScore" width-class="w-72">
          <template #trigger="{ open, tooltipId }">
            <button type="button" class="inline-flex h-7 w-7 items-center justify-center hover:bg-gray-500/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500" aria-label="查看日总分计算方式" :aria-describedby="open ? tooltipId : undefined"><Icon name="infoCircle" size="sm" aria-hidden="true" /></button>
          </template>
          日总分按已观察维度的原权重归一计算，未观察维度不补分。
        </HelpTooltip>
      </span></div>
      <div v-for="dimension in report.dimensions" :key="dimension.code" class="dimension-score">
        <span>{{ dimension.name }}</span><strong>{{ dimension.score ?? '—' }}</strong>
      </div>
    </section>

    <section class="assessment-content" aria-labelledby="assessment-title">
      <h2 id="assessment-title">评分依据与任务解读</h2>
      <nav class="dimension-nav" aria-label="评审维度">
        <button v-for="dimension in report.dimensions" :key="dimension.code" type="button"
          :aria-pressed="active === dimension.code" :class="{ selected: active === dimension.code }"
          @click="active = dimension.code">{{ dimension.name }}</button>
      </nav>
      <div v-for="item in visibleItems" :key="item.item_code" class="item-review">
        <header class="item-heading"><h3>{{ item.name }}</h3><span>{{ item.score === null ? '不单独计分' : `${item.score} 分` }}</span></header>
        <div class="reading-columns">
          <div class="case-evidence">
            <template v-for="evidence in caseGroups(item)" :key="evidence.key">
              <article class="source-excerpt">
                <h4>
                  <button v-if="evidence.task" type="button" class="task-link" aria-haspopup="dialog"
                    :aria-label="`查看任务：${evidence.title}`" @click="selectedCase = evidence">{{ evidence.title }}</button>
                  <template v-else>{{ evidence.title }}</template>
                </h4>
                <span class="content-label">用户原话</span>
                <blockquote>
                  <p v-for="message in evidence.messages" :key="message.key" class="quote-message">{{ quotedText(message) }}</p>
                </blockquote>
              </article>
            </template>
          </div>
          <div class="ai-assessment">
            <span class="content-label">AI 评语</span>
            <template v-for="(section, index) in explanation(item, display)" :key="index">
              <ReportProse :text="section.analysis.join('')" />
              <div v-if="section.basis.length" class="next-band">
                <h4>{{ item.score === null ? '不计分说明' : '距离下一档还差什么' }}</h4>
                <ReportProse :text="section.basis.join('')" />
              </div>
            </template>
          </div>
        </div>
        <div class="related-tasks">
          <template v-for="evidence in caseGroups(item)" :key="evidence.key">
            <template v-if="!evidence.task">
              <details v-for="message in evidence.messages.filter(hasExtraInput)" :key="message.key" class="original-message">
                <summary>查看该条原消息</summary>
                <pre>{{ message.context }}</pre>
              </details>
            </template>
          </template>
        </div>
      </div>
    </section>
    <BaseDialog :show="!!selectedCase?.task" :title="selectedCase?.title ?? ''" width="wide" @close="selectedCase = null">
      <div v-if="selectedCase?.task" class="task-detail">
        <section v-if="selectedCase.task.goal">
          <h4>任务背景</h4>
          <p class="task-goal">{{ selectedCase.task.goal }}</p>
        </section>
        <section v-if="selectedCase.task.workflow.length" class="task-workflow">
          <h4>Skill、MCP 与工作流</h4>
          <dl>
            <template v-for="(workflow, index) in selectedCase.task.workflow" :key="index">
              <dt>{{ workflow.name }}<span>{{ workflow.selection }}</span></dt>
              <dd>{{ workflow.purpose }}</dd>
            </template>
          </dl>
        </section>
        <section v-if="selectedCase.task.records?.length">
          <h4>相关对话</h4>
          <AssessmentContext :records="selectedCase.task.records" :cited-sources="selectedCase.messages.map(message => message.source_ref)" />
        </section>
        <template v-else>
          <details v-for="message in selectedCase.messages.filter(hasExtraInput)" :key="message.key" class="original-message">
            <summary>查看该条原消息</summary>
            <pre>{{ message.context }}</pre>
          </details>
        </template>
      </div>
    </BaseDialog>
  </div>
</template>

<script lang="ts">
import type { AssessmentContextRecord } from './AssessmentContext.vue'
export interface AssessmentTask {
  title: string
  goal: string
  outcome: string
  workflow: { name: string; purpose: string; selection: string; user_refs: string[]; tool_refs: string[] }[]
  records?: AssessmentContextRecord[]
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Item, Report } from './api'
import ReportProse from './ReportProse.vue'
import AssessmentContext from './AssessmentContext.vue'
import BaseDialog from '@/components/common/BaseDialog.vue'
import HelpTooltip from '@/components/common/HelpTooltip.vue'
import Icon from '@/components/icons/Icon.vue'
import { evidenceForDisplay, explanation, reportText } from './reportPresentation'

const props = withDefaults(defineProps<{ report: Report; periodLabel: string; sourceLabels?: Record<string, string>; sourceSessions?: Record<string, string>; conversations?: Record<string, AssessmentTask> }>(), { sourceLabels: () => ({}), sourceSessions: () => ({}), conversations: () => ({}) })
const active = ref('P')
const selectedCase = ref<CaseGroup | null>(null)
watch(() => props.report, (report) => {
  selectedCase.value = null
  if (!report.dimensions.some(d => d.code === active.value)) active.value = report.dimensions[0]?.code ?? ''
}, { immediate: true })
const visibleItems = computed(() => (props.report.items ?? []).filter(item => item.item_code.startsWith(active.value)))
const display = computed(() => reportText(props.report))
const partialDailyScore = computed(() => props.report.period_type === 'day' && props.report.score !== null && props.report.dimensions.some(d => d.score === null))
interface QuotedMessage { key: string; source_ref: string; context: string; quotes: string[] }
interface CaseGroup { key: string; title: string; task?: AssessmentTask; messages: QuotedMessage[] }
function caseGroups(item: Item) {
  const groups = new Map<string, CaseGroup>()
  for (const record of evidenceForDisplay(item)) {
    const messageKey = JSON.stringify([record.source_ref, record.source_hash])
    // 只按真实会话关联任务，不凭相同标题归并不同对话。
    const session = props.sourceSessions[record.source_ref]
    const key = session ? `session:${session}` : messageKey
    const task = session ? props.conversations[session] : undefined
    const group = groups.get(key) ?? { key, task, title: task?.title ?? props.sourceLabels[record.source_ref] ?? record.task_title, messages: [] }
    let message = group.messages.find(message => message.key === messageKey)
    if (!message) {
      message = { key: messageKey, source_ref: record.source_ref, context: record.context, quotes: [] }
      group.messages.push(message)
    }
    if (!message.quotes.includes(record.quote)) message.quotes.push(record.quote)
    groups.set(key, group)
  }
  return [...groups.values()]
}
function quotedText(message: QuotedMessage) {
  const quotes = [...message.quotes].sort((a, b) => message.context.indexOf(a) - message.context.indexOf(b))
  let end = 0
  return quotes.map((quote, index) => {
    const start = message.context.indexOf(quote)
    const gap = index && start > end ? message.context.slice(end, start) : ''
    const text = index && start < end ? quote.slice(end - start) : quote
    end = Math.max(end, start + quote.length)
    return (gap.trim() ? ' … ' : gap) + text
  }).join('')
}
const plain = (text: string) => text.replace(/\s+/g, '')
const hasExtraInput = (message: QuotedMessage) => plain(message.context) !== plain(quotedText(message))
</script>

<style scoped>
.assessment-reader{color:#24272c;font-size:15px;line-height:1.75}
.score-overview{display:grid;grid-template-columns:1.25fr repeat(4,1fr);align-items:center;gap:24px;padding:24px 0 32px;border-bottom:1px solid #dce1e5}
.total-score,.dimension-score{display:flex;flex-direction:column;align-items:flex-start;font-size:13px;color:#59616b}
.total-score{border-right:1px solid #dce1e5}.total-score strong{font-size:56px;line-height:1.2;font-weight:600;color:#1e3037;font-variant-numeric:tabular-nums}
.dimension-score strong{margin-top:8px;font-size:30px;line-height:1.3;font-weight:500;color:#283c43;font-variant-numeric:tabular-nums}
.assessment-content{padding-top:28px}.assessment-content>h2{font-size:21px;font-weight:600;line-height:1.5;margin:0 0 16px}
.dimension-nav{display:flex;gap:24px;border-bottom:1px solid #dce1e5}.dimension-nav button{padding:10px 2px 12px;border-bottom:3px solid transparent;color:#59616b;font-size:15px;text-align:left;background:transparent;cursor:pointer}
.dimension-nav button.selected{border-bottom-color:#286052;color:#254b41;font-weight:600}.dimension-nav button:hover{color:#254b41}.dimension-nav button:focus-visible,summary:focus-visible{outline:2px solid #286052;outline-offset:4px}
.item-review{padding:24px 0 28px;border-bottom:1px solid #e4e7e9}.item-heading{display:flex;align-items:baseline;gap:16px;margin-bottom:16px}.item-heading h3{font-size:18px;font-weight:600;margin:0}.item-heading>span{font-size:15px;color:#495d63;font-variant-numeric:tabular-nums}
.reading-columns{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:28px}.case-evidence:empty{display:none}.case-evidence:empty+.ai-assessment{grid-column:1/-1;max-width:760px}
.source-excerpt+.source-excerpt{margin-top:20px}.source-excerpt h4{font-weight:500;font-size:14px;color:#535c65;margin:0 0 8px}.content-label{display:block;font-size:12px;font-weight:600;margin-bottom:8px;color:#53616a}
.task-link{color:#286052;text-align:left;text-decoration:underline;text-underline-offset:3px;cursor:pointer;background:transparent}.task-link:hover{color:#1e443a}.task-link:focus-visible{outline:2px solid #286052;outline-offset:4px}
.task-detail{color:#303b42;font-size:14px;line-height:1.65}.task-detail section+section{border-top:1px solid #e4e7e9;padding-top:16px;margin-top:20px}.task-detail h4{font-size:14px;font-weight:600;margin:0 0 10px;color:#53616a}
.task-goal{white-space:pre-wrap;margin:0}.task-workflow dl{margin:0}.task-workflow dt{font-weight:600;overflow-wrap:anywhere}.task-workflow dt span{display:block;font-size:12px;font-weight:400;color:#59616b}.task-workflow dd{margin:2px 0 12px;color:#47535b;line-height:1.7}.task-workflow dd:last-child{margin-bottom:0}
blockquote{overflow-wrap:anywhere;margin:0;padding:10px 12px;background:#f3f5f6;border-left:3px solid #c7d0d5;line-height:1.55}
.quote-message{white-space:pre-wrap;margin:0}.quote-message+.quote-message{margin-top:6px}
.related-tasks:empty{display:none}
details{font-size:13px;margin-top:8px}summary{cursor:pointer;color:#475c65;width:fit-content}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;background:#f3f5f6;padding:12px;margin-top:8px}
.ai-assessment{min-width:0}.ai-assessment>.content-label{color:#286052}.next-band{margin-top:20px;padding-top:12px;border-top:1px solid #e4e7e9}.next-band h4{font-size:13px;font-weight:600;color:#53616a;margin-bottom:6px}
</style>
