<template>
  <details class="text-xs leading-5 text-gray-500 dark:text-gray-400">
    <summary class="cursor-pointer">工作日计分 · {{ scope.working_dates.length }} 天</summary>
    <dl class="workday-details mt-2 whitespace-normal tabular-nums">
      <dt>计分日期</dt><dd class="flex flex-wrap gap-x-3"><span v-for="day in scope.working_dates" :key="day" class="whitespace-nowrap">{{ day }}</span><span v-if="!scope.working_dates.length">无计分日期</span></dd>
      <dt>计分截止</dt><dd>{{ formatTime(scope.observed_until) }}</dd>
      <dt>来源封口</dt><dd>{{ formatTime(scope.freeze_at) }}</dd>
      <template v-if="scope.candidate_due_at"><dt>候选准备期限</dt><dd>{{ formatTime(scope.candidate_due_at) }}</dd></template>
      <dt>时区</dt><dd>北京时间</dd>
    </dl>
  </details>
</template>
<script setup lang="ts">
import type { WorkdayScope } from './api'
defineProps<{scope:WorkdayScope}>()
const formatTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
</script>
<style scoped>
.workday-details{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 12px;max-width:620px}
.workday-details dt{white-space:nowrap}.workday-details dd{min-width:0;margin:0}
</style>
