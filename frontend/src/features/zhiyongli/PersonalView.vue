<template>
  <AppLayout>
    <div class="space-y-6">
      <header class="flex flex-wrap items-center justify-between gap-4">
        <p class="text-sm text-gray-500 dark:text-gray-400">每日查看维度分析，每周形成综合评分。</p>
        <div class="flex items-center gap-3">
          <div class="inline-flex gap-1 rounded-lg bg-white p-1 ring-1 ring-gray-200 dark:bg-dark-800 dark:ring-dark-600" role="group" aria-label="评分周期类型">
            <button v-for="option in periodOptions" :key="option.value" type="button" class="rounded-md px-4 py-2 text-sm font-medium focus-visible:outline-primary-500" :class="periodType === option.value ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400' : 'text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-dark-700'" :aria-pressed="periodType === option.value" @click="changePeriod(option.value)">{{ option.label }}</button>
          </div>
          <Select v-if="reports.length" v-model="selectedId" :options="reportOptions" class="w-72" :aria-label="periodType === 'day' ? '选择评分日期' : '选择评分周期'" @change="loadReport" />
          <RouterLink :to="{path:'/zhiyongli/rules',query:{version:report?.policy_versions.rules,period_type:periodType,aggregation:report?.policy_versions.aggregation}}" class="btn btn-secondary">评分规则</RouterLink>
        </div>
      </header>
      <LoadingSpinner v-if="loading" class="mx-auto my-12" />
      <div v-else-if="error" class="py-10 text-center"><p class="text-sm text-red-600">{{ error }}</p><button class="btn btn-secondary mt-4" @click="resetPeriod">重试</button></div>
      <ReportBody v-else-if="report" :key="report.id" :report="report" reveal-score />
      <EmptyState v-else :title="periodType === 'day' ? '暂无已发布的每日分析' : periodType === 'week' ? '暂无已发布的周评分' : '暂无已发布的月评级'" />
      <div v-if="total > reports.length" class="flex justify-center"><button class="btn btn-secondary" :disabled="loading" @click="loadMore">更早周期</button></div>
    </div>
  </AppLayout>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppLayout from '@/components/layout/AppLayout.vue'
import Select from '@/components/common/Select.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import ReportBody from './ReportBody.vue'
import { listReports, getReport, periodLabel, type Report } from './api'
const periodOptions = [{value:'week',label:'周评分'},{value:'day',label:'每日分析'},{value:'month',label:'月评级'}]
const periodType = ref('week'), selectedId = ref<number | null>(null), reports = ref<Report[]>([]), report = ref<Report | null>(null)
const loading = ref(false), error = ref(''), total = ref(0), page = ref(1)
let request = 0
const reportOptions = computed(() => reports.value.map(r => ({value:r.id,label:periodLabel(r)})))
function changePeriod(value:string) { if(periodType.value !== value) { periodType.value=value;void resetPeriod() } }
// 统一请求层传来的实际错误信息，不改写成“接口暂未支持”。
async function loadReport() {
  const current = ++request
  report.value = null;error.value = '';loading.value = true
  try { if(selectedId.value !== null) {const result = await getReport(false, selectedId.value);if(current===request) report.value=result} }
  catch(e) { if(current===request) error.value=(e as {message:string}).message }
  finally { if(current===request) loading.value=false }
}
async function resetPeriod() {
  const current = ++request
  loading.value=true;error.value='';report.value=null;reports.value=[];selectedId.value=null;page.value=1;total.value=0
  try {
    const result = await listReports(false,{period_type:periodType.value,page:1})
    if(current!==request) return
    reports.value=result.items;total.value=result.total;selectedId.value=result.items[0]?.id ?? null
    await loadReport()
  }catch(e){if(current===request)error.value=(e as {message:string}).message}
  finally {if(current===request)loading.value=false}
}
async function loadMore() {
  const current=++request;loading.value=true;error.value=''
  try {const result=await listReports(false,{period_type:periodType.value,page:page.value+1});if(current===request){reports.value.push(...result.items);total.value=result.total;page.value++}}
  catch(e){if(current===request)error.value=(e as {message:string}).message}
  finally{if(current===request)loading.value=false}
}
onMounted(resetPeriod)
</script>
