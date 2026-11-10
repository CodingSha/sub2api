<template>
  <AppLayout>
    <div class="space-y-6">
      <header class="flex flex-wrap items-center justify-end gap-4">
        <div class="flex items-center gap-3">
          <Select v-model="periodType" :options="periodOptions" class="w-28" aria-label="评分周期类型" @change="resetPeriod" />
          <Select v-if="reports.length" v-model="selectedId" :options="reportOptions" class="w-72" aria-label="选择评分周期" @change="loadReport" />
          <RouterLink :to="{path:'/zhiyongli/rules',query:{version:report?.policy_versions.rules}}" class="btn btn-secondary">评分规则</RouterLink>
        </div>
      </header>
      <LoadingSpinner v-if="loading" class="mx-auto my-12" />
      <div v-else-if="error" class="py-10 text-center"><p class="text-sm text-red-600">{{ error }}</p><button class="btn btn-secondary mt-4" @click="resetPeriod">重试</button></div>
      <ReportBody v-else-if="report" :key="report.id" :report="report" />
      <EmptyState v-else :title="periodType === 'week' ? '暂无已发布的周评分' : '暂无已发布的月评分'" description="评分完成并由管理员发布后，将在这里展示。" />
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
const periodOptions = [{value:'week',label:'周评分'},{value:'month',label:'月评分'}]
const periodType = ref('week'), selectedId = ref<number | null>(null), reports = ref<Report[]>([]), report = ref<Report | null>(null)
const loading = ref(false), error = ref(''), total = ref(0), page = ref(1)
let request = 0
const reportOptions = computed(() => reports.value.map(r => ({value:r.id,label:periodLabel(r)})))
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
