<template>
  <details class="text-xs leading-5 text-gray-500 dark:text-gray-400">
    <summary class="cursor-pointer">工作日计分 · {{ scope.working_dates.length }} 天</summary>
    <dl class="mt-1 max-w-md space-y-1 whitespace-normal tabular-nums">
      <div><dt class="inline">计分日期：</dt><dd class="inline">{{ scope.working_dates.join('、') || '无计分日期' }}</dd></div>
      <div><dt class="inline">计分截止：</dt><dd class="inline">{{ formatTime(scope.observed_until) }}</dd></div>
      <div><dt class="inline">来源封口：</dt><dd class="inline">{{ formatTime(scope.freeze_at) }}</dd></div>
      <div v-if="scope.candidate_due_at"><dt class="inline">候选准备期限：</dt><dd class="inline">{{ formatTime(scope.candidate_due_at) }}</dd></div>
    </dl>
  </details>
</template>
<script setup lang="ts">
import type { WorkdayScope } from './api'
defineProps<{scope:WorkdayScope}>()
const formatTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）'
</script>
