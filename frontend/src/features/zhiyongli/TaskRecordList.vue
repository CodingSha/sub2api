<template>
  <ol class="task-records" aria-label="任务对话记录">
    <li v-for="{record,occurrences,limitations} in groups" :key="record.id" class="record-entry border-b border-gray-200 last:border-0 dark:border-dark-700">
      <div class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
        <span class="font-semibold" :class="record.role === 'user' ? 'text-gray-900 dark:text-gray-100' : ''">{{ roles[record.role] }}</span>
        <span v-if="occurrences.length > 1">同文 {{ occurrences.length }} 条</span>
        <template v-else><time v-if="record.occurred_at" :datetime="record.occurred_at">{{ formatTime(record.occurred_at) }}</time><span v-else>时间未知</span></template>
        <span v-if="record.origin === 'unattributed'">背景记录，不作为本人表现</span>
      </div>
      <details v-if="occurrences.length > 1" class="mb-3 text-xs leading-6 text-gray-500 dark:text-gray-400">
        <summary class="cursor-pointer">查看每次记录</summary>
        <ol><li v-for="(occurrence,index) in occurrences" :key="occurrence.id">第 {{ index + 1 }} 条 · <time v-if="occurrence.occurred_at" :datetime="occurrence.occurred_at">{{ formatTime(occurrence.occurred_at) }}</time><span v-else>时间未知</span></li></ol>
      </details>
      <p v-if="!record.text.trim()" class="text-sm text-amber-700 dark:text-amber-400">这条记录未保存正文，无法确认其内容。</p>
      <details v-else-if="record.text.length > 1400 || record.role === 'tool_call' || record.role === 'tool_result'" class="long-record">
        <summary class="text-sm font-medium text-primary-700 dark:text-primary-400">展开完整{{ roles[record.role] }}（{{ record.text.length }} 字）</summary>
        <ReportProse class="record-body mt-3" :text="record.text" />
      </details>
      <ReportProse v-else class="record-body" :text="record.text" />
      <p v-for="limit in limitations" :key="limit" class="mt-2 text-xs leading-5 text-amber-700 dark:text-amber-400">{{ limit }}</p>
    </li>
  </ol>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ReportProse from './ReportProse.vue'
import { groupRecordText } from './reportText'
import type { TaskRecord } from './api'
const props = defineProps<{ records: TaskRecord[] }>()
const groups = computed(() => groupRecordText(props.records))
const roles = { user: '本人输入', assistant: 'AI 回复', tool_call: '工具调用', tool_result: '工具结果', context: '关联背景' }
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
</script>

<style scoped>
.record-entry { padding: .8rem 0; }
.record-body { font-size: 14px; line-height: 1.65; overflow-wrap: anywhere; color: inherit; }
.long-record > summary { cursor: pointer; padding: .25rem 0; }
</style>
