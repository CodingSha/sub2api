<template>
  <section v-if="cases.length" class="pt-7" aria-label="评分依据与任务解读">
    <h2 class="mb-4 text-lg font-semibold">评分依据与任务解读</h2>
    <details v-for="(entry, index) in visibleCases" :key="entry.key" :open="index === 0" class="border-b border-gray-200 dark:border-dark-700">
      <summary class="flex cursor-pointer items-start gap-3 py-4 focus-visible:outline-primary-500">
        <span class="w-6 shrink-0 text-sm tabular-nums text-gray-400">{{ page * pageSize + index + 1 }}</span>
        <span class="min-w-0 flex-1 font-medium">{{ entry.record.task_title }}</span>
        <Icon name="chevronDown" size="sm" class="case-arrow mt-1 shrink-0 text-gray-500" aria-hidden="true" />
      </summary>
      <article class="space-y-5 pb-6 pl-9 text-sm leading-7">
        <div v-for="assessment in entry.assessments" :key="assessment.fact">
          <div class="mb-2 flex flex-wrap gap-2"><span v-for="name in assessment.items" :key="name" class="badge badge-gray">{{ name }}</span></div>
          <p class="whitespace-pre-wrap break-words">{{ assessment.fact }}</p>
        </div>
        <div>
          <h3 class="mb-2 text-xs font-medium text-gray-500 dark:text-gray-400">对话依据 · {{ formatTime(entry.record.occurred_at) }}</h3>
          <blockquote class="whitespace-pre-wrap break-words border-l-2 border-primary-400 pl-4">{{ entry.record.quote }}</blockquote>
          <details v-if="sourceContext(entry.record) !== entry.record.quote" class="mt-3">
            <summary class="cursor-pointer text-primary-700 dark:text-primary-400">查看前后文</summary>
            <p class="mt-3 whitespace-pre-wrap break-words text-gray-600 dark:text-gray-400">{{ sourceContext(entry.record) }}</p>
          </details>
        </div>
        <ul v-if="admin && entry.record.limitations?.length" class="list-disc pl-4 text-amber-700 dark:text-amber-400"><li v-for="limit in entry.record.limitations" :key="limit">{{ limit }}</li></ul>
      </article>
    </details>
    <nav v-if="cases.length > pageSize" class="mt-4 flex items-center justify-between gap-4 text-sm" aria-label="评分依据分页">
      <span class="text-gray-500">第 {{ page * pageSize + 1 }}–{{ Math.min((page + 1) * pageSize, cases.length) }} 条，共 {{ cases.length }} 条依据</span>
      <div class="flex gap-3">
        <button class="btn btn-secondary btn-sm" :disabled="page === 0" @click="page--">上一页</button>
        <button class="btn btn-secondary btn-sm" :disabled="(page + 1) * pageSize >= cases.length" @click="page++">下一页</button>
      </div>
    </nav>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Icon from '@/components/icons/Icon.vue'
import type { Report } from './api'
import { evidenceCases, sourceContext } from './reportPresentation'
const props = defineProps<{ report: Report; admin?: boolean }>()
const cases = computed(() => evidenceCases(props.report, props.admin))
const page = ref(0)
const pageSize = 6
const visibleCases = computed(() => cases.value.slice(page.value * pageSize, (page.value + 1) * pageSize))
watch(() => props.report, () => { page.value = 0 })
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
</script>

<style scoped>
summary{list-style:none}summary::-webkit-details-marker{display:none}.case-arrow{transition:transform 160ms}details[open]>summary .case-arrow{transform:rotate(180deg)}
@media(prefers-reduced-motion:reduce){.case-arrow{transition:none}}
</style>
