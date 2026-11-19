<template>
  <section v-if="review" class="pt-8" aria-label="任务与评分依据">
    <header class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-[22px] font-semibold leading-8">评分依据与任务解读</h2>
      <div class="flex items-center gap-3 text-sm text-gray-600 dark:text-gray-400">
        <span>{{ review.status === 'source_linked' ? '关联来源' : '本周识别' }} {{ review.task_keys.length }} {{ review.status === 'source_linked' ? '组' : '个任务' }}</span>
        <span>展示 {{ review.cases.length }} 个案例</span>

      </div>
    </header>
    <nav v-if="review.cases.length > 1" class="mb-6 flex flex-wrap gap-2" aria-label="选择任务案例">
      <button v-for="(task,index) in review.cases" :key="task.id" type="button" class="rounded border px-3 py-2 text-left text-sm focus-visible:outline-primary-500" :class="selectedId === task.id ? 'border-primary-500 text-primary-700 dark:text-primary-400' : 'border-gray-200 text-gray-600 dark:border-dark-700 dark:text-gray-400'" :aria-pressed="selectedId === task.id" @click="selectedId = task.id">{{ index + 1 }}. {{ task.title }}</button>
    </nav>
    <TaskCaseArticle v-if="selected" :key="`${report.id}-${report.revision}-${selected.id}-${admin}`" :report="report" :task="selected" :admin="admin" />
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import TaskCaseArticle from './TaskCaseArticle.vue'
import type { Report } from './api'

const props = defineProps<{ report: Report; admin?: boolean }>()
const review = computed(() => {
  const value = props.report.task_review
  if (!value) return undefined
  const formal = value.version === 'task-evidence-20261106.1' && ['case_checked', 'week_checked', 'generated'].includes(value.status)
  const linked = value.version === 'source-evidence-20261116.1' && value.status === 'source_linked'
  return formal || linked ? value : undefined
})
const selectedId = ref('')
watch(() => [props.report.id, props.report.revision, review.value] as const, () => {
  selectedId.value = review.value?.cases[0]?.id ?? ''
}, { immediate: true })
const selected = computed(() => review.value?.cases.find(task => task.id === selectedId.value))
</script>
