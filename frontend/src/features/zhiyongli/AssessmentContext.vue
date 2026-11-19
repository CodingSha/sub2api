<template>
  <ol class="context-records" aria-label="相关对话记录">
    <li v-for="record in records" :key="record.id" :class="{ cited: citedSources.includes(record.id) }">
      <header>
        <span>{{ roles[record.role] }}</span>
        <span v-if="citedSources.includes(record.id)" class="citation-label">本项引用</span>
        <span v-if="record.excerpt" class="excerpt-label">原文节选</span>
      </header>
      <details v-if="record.role === 'tool' || record.text.length > 800" class="long-context">
        <summary>
          <span>{{ record.role === 'tool' ? record.names.join('、') || '查看工具记录' : '展开这条回复' }}</span>
          <span v-if="record.role !== 'tool'" class="context-preview">{{ record.text.slice(0, 180) }}…</span>
        </summary>
        <ReportProse :text="record.text" />
      </details>
      <ReportProse v-else :text="record.text" />
    </li>
  </ol>
</template>

<script lang="ts">
export interface AssessmentContextRecord {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'context'
  text: string
  excerpt: boolean
  source_order: number
  source_hash: string
  names: string[]
}
</script>

<script setup lang="ts">
import ReportProse from './ReportProse.vue'
defineProps<{ records: AssessmentContextRecord[]; citedSources: string[] }>()
const roles = { user: '用户', assistant: 'AI 回复', tool: '工具记录', context: '关联背景' }
</script>

<style scoped>
.context-records{padding:0 16px;margin:0;list-style:none;background:#f7f8f8}
.context-records>li{padding:12px 0;border-bottom:1px solid #e0e5e5}
.context-records>li:last-child{border-bottom:0}
.context-records>li.cited{border-left:2px solid #73958a;padding-left:12px;margin-left:-12px}
header{display:flex;align-items:center;gap:12px;margin-bottom:6px;font-size:12px;font-weight:600;color:#53616a}
.citation-label{font-weight:400;color:#286052}.excerpt-label{font-weight:400;color:#59616b}
.report-prose{font-size:14px;line-height:1.65;color:#303b42}
summary{cursor:pointer;color:#475c65;font-size:13px;line-height:1.6;overflow-wrap:anywhere}
summary:focus-visible{outline:2px solid #286052;outline-offset:3px}
.context-preview{display:block;font-size:14px;color:#59616b;white-space:pre-wrap;margin-top:4px}
.long-context[open] .context-preview{display:none}
.long-context[open] .report-prose{margin-top:8px}
</style>
