<template>
  <AppLayout>
    <div class="space-y-5">
      <header class="flex items-center justify-between border-b border-gray-200 dark:border-dark-700">
        <nav class="flex gap-6" aria-label="智用力管理">
          <button v-for="tab in tabs" :key="tab.value" class="border-b-2 py-3 text-sm font-medium focus-visible:outline-primary-500" :class="activeTab === tab.value ? 'border-primary-500 text-primary-700 dark:text-primary-400' : 'border-transparent text-gray-500 dark:text-gray-400'" :aria-current="activeTab === tab.value ? 'page' : undefined" @click="switchTab(tab.value)">{{ tab.label }}</button>
        </nav>
        <div class="flex gap-3">
          <RouterLink to="/zhiyongli/rules" class="btn btn-secondary">评分规则</RouterLink>
          <button class="btn btn-secondary" :disabled="importing" @click="fileInput?.click()">{{ importing ? '接入中…' : '导入结果' }}</button>
          <button class="btn btn-primary" @click="beginReview">开始评审</button>
          <input ref="fileInput" type="file" accept="application/json,.json" class="hidden" aria-label="选择冻结评测结果包" @change="importFile" />
        </div>
      </header>
      <div v-if="activeTab === 'reports'" class="flex flex-wrap items-center gap-3">
        <Select v-model="periodType" :options="periodOptions" class="w-28" aria-label="周期类型" @change="resetList" />
        <label class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">周期开始<input v-model="periodStart" type="date" class="input w-40" aria-label="筛选周期开始日期" @change="resetList" /></label>
        <Select v-model="status" :options="statusOptions" class="w-36" aria-label="发布状态" @change="resetList" />
        <form class="flex gap-2" @submit.prevent="resetList"><input v-model="search" class="input w-64" placeholder="账户邮箱" aria-label="搜索账户邮箱" /><button class="btn btn-secondary">查询</button></form>
        <button class="btn btn-secondary" :disabled="preparingBatch || batchPublishing || status === 'published' || status === 'superseded'" @click="prepareBatch">{{ preparingBatch ? '读取中…' : '批量发布' }}</button>
        <span class="ml-auto text-sm text-gray-500 dark:text-gray-400">{{ total }} 份结果</span>
      </div>
      <div v-if="error" class="flex items-center gap-3 text-sm text-red-600"><span>{{ error }}</span><button class="btn btn-secondary" @click="load">重试</button></div>
      <DataTable v-if="activeTab === 'reports'" :columns="columns" :data="reports" :loading="loading">
        <template #cell-user_email="{row}"><button class="max-w-48 whitespace-normal break-all text-left font-medium text-primary-700 hover:underline focus-visible:outline-primary-500 dark:text-primary-400" @click="openReport(row.id)">{{ row.user_email }}</button></template>
        <template #cell-period_start="{row}"><div class="text-xs leading-5 tabular-nums"><div>{{ row.period_start }}</div><div class="text-gray-500 dark:text-gray-400">至 {{ periodLabel(row).split(' — ')[1] }}</div></div></template>
        <template #cell-score="{row}">
          <span v-if="row.score !== null" class="font-semibold tabular-nums">{{ row.score }}</span>
          <HelpTooltip v-else width-class="w-80">
            <template #trigger="{open, tooltipId}">
              <button type="button" class="inline-flex min-h-8 items-center gap-1 text-xs focus-visible:outline-primary-500" :class="scoreNeedsAttention(row) ? 'text-orange-700 dark:text-orange-400' : 'text-gray-500 dark:text-gray-400'" :aria-describedby="open ? tooltipId : undefined">
                <Icon :name="scoreNeedsAttention(row) ? 'exclamationTriangle' : 'infoCircle'" size="sm" aria-hidden="true" />{{ scoreStatusLabel(row) }}
              </button>
            </template>
            <ScoreExplanation :report="row" />
          </HelpTooltip>
        </template>
        <template #cell-rating="{row}">{{ row.period_type === 'week' || row.score_status === 'observation_only' || row.score_status === 'no_activity' ? '—' : row.rating === null ? '待评级' : row.rating }}</template>
        <template v-for="code in dimensionCodes" :key="code" #[`cell-${code}`]="{row}"><span class="tabular-nums">{{ row.dimensions.find((d: Dimension) => d.code === code)?.score ?? '—' }}</span></template>
        <template #cell-publication_status="{row}"><span class="badge" :class="row.publication_status === 'published' ? 'badge-success' : 'badge-gray'">{{ statusText[row.publication_status as keyof typeof statusText] }}</span></template>
        <template #cell-actions="{row}"><button class="text-sm text-primary-700 dark:text-primary-400" @click="openReport(row.id)">查看</button></template>
        <template #empty><p class="text-sm">没有符合条件的评分结果</p></template>
      </DataTable>
      <DataTable v-else-if="activeTab === 'runs'" :columns="runColumns" :data="runs" :loading="loading">
        <template #cell-created_at="{value}">{{ formatTime(value) }}</template>
        <template #cell-source_name="{value}"><span class="block max-w-lg whitespace-normal break-words">{{ value }}</span></template>
        <template #cell-digest="{value}"><HelpTooltip width-class="w-80"><template #trigger="{open, tooltipId}"><button type="button" class="min-h-8 font-mono text-xs text-gray-600 focus-visible:outline-primary-500 dark:text-gray-300" aria-label="查看完整校验值" :aria-describedby="open ? tooltipId : undefined">{{ value.slice(0,16) }}</button></template><span class="break-all">{{ value }}</span></HelpTooltip></template>
        <template #empty><p class="text-sm">暂无接入记录</p></template>
      </DataTable>
      <ReviewJobs v-if="activeTab === 'jobs'" :start-open="startOpen" @close="startOpen=false" @reports="showJobReports" />
      <div v-if="activeTab !== 'jobs' && total > 20" class="flex items-center justify-end gap-3 text-sm"><button class="btn btn-secondary" :disabled="page === 1 || loading" @click="changePage(-1)">上一页</button><span>{{ page }} / {{ Math.ceil(total/20) }}</span><button class="btn btn-secondary" :disabled="page*20 >= total || loading" @click="changePage(1)">下一页</button></div>
      <BaseDialog :show="detailOpen" :title="detail ? detail.user_email : '评分报告'" width="extra-wide" @close="detailOpen=false">
        <LoadingSpinner v-if="detailLoading" class="mx-auto my-12" />
        <p v-else-if="detailError" class="text-red-600">{{ detailError }}</p>
        <template v-else-if="detail">
          <div class="mb-5 flex items-center justify-between gap-4 text-sm"><span>{{ periodLabel(detail) }} · {{ statusText[detail.publication_status] }}</span><RouterLink :to="{path:'/zhiyongli/rules',query:{version:detail.policy_versions.rules}}" class="text-primary-700 dark:text-primary-400">评分规则</RouterLink></div>
          <ReportBody :key="detail.id" :report="detail" review-details />
        </template>
        <template #footer>
          <div class="flex items-center justify-end gap-3"><span v-if="detail?.publication_status === 'draft'" class="mr-auto text-sm text-gray-500">检查评分与依据后发布，用户即可查看。</span><button class="btn btn-secondary" @click="detailOpen=false">关闭</button><button v-if="detail?.publication_status === 'draft'" class="btn btn-primary" :disabled="publishing" @click="publish">{{ publishing ? '发布中…' : '确认发布' }}</button></div>
        </template>
      </BaseDialog>
      <BaseDialog :show="batchOpen" title="批量发布" width="normal" :close-on-escape="!batchPublishing" :show-close-button="!batchPublishing" @close="closeBatch">
        <div class="space-y-4">
          <label class="block text-sm">发布周期<Select v-model="batchStart" :options="batchPeriods" class="mt-2" :disabled="batchPublishing" aria-label="批量发布周期" /></label>
          <p class="text-sm">将发布以下 {{ batchReports.length }} 份待发布结果（{{ batchScoredCount }} 份有总分，{{ batchReports.length-batchScoredCount }} 份无总分）。发布后用户可查看；无总分的结果会保留维度分和原因说明。</p>
          <ul class="max-h-60 overflow-y-auto border-y border-gray-200 py-2 text-sm leading-7 dark:border-dark-700"><li v-for="report in batchReports" :key="report.id">{{ report.user_email }}</li></ul>
          <p class="text-xs text-gray-500">范围为当前账户查询条件下、所选周期的全部待发布结果，包含其他分页。任一报告版本发生变化或检查不通过，整批不发布。</p>
          <p v-if="batchError" role="alert" class="text-sm text-red-600">{{ batchError }}</p>
        </div>
        <template #footer><div class="flex justify-end gap-3"><button class="btn btn-secondary" :disabled="batchPublishing" @click="closeBatch">取消</button><button class="btn btn-primary" :disabled="batchPublishing || !batchReports.length" @click="publishBatch">{{ batchPublishing ? '发布中…' : '确认发布' }}</button></div></template>
      </BaseDialog>
    </div>
  </AppLayout>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppLayout from '@/components/layout/AppLayout.vue'
import Select from '@/components/common/Select.vue'
import DataTable from '@/components/common/DataTable.vue'
import BaseDialog from '@/components/common/BaseDialog.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import HelpTooltip from '@/components/common/HelpTooltip.vue'
import Icon from '@/components/icons/Icon.vue'
import { useAppStore } from '@/stores/app'
import ReportBody from './ReportBody.vue'
import ScoreExplanation from './ScoreExplanation.vue'
import ReviewJobs from './ReviewJobs.vue'
import { listReports, getReport, listRuns, importResults, publishReport, batchPublishReports, periodLabel, statusText, type Dimension, type Report, type Run } from './api'
import { dimensionNames, scoreNeedsAttention, scoreStatusLabel } from './reportPresentation'
const app = useAppStore()
const tabs=[{value:'reports',label:'评分结果'},{value:'jobs',label:'评审进度'},{value:'runs',label:'接入记录'}]
const startOpen=ref(false)
const activeTab=ref('reports'),periodType=ref('week'),status=ref(''),search=ref(''),page=ref(1),total=ref(0)
const periodStart=ref('')
const reports=ref<Report[]>([]),runs=ref<Run[]>([]),loading=ref(false),error=ref(''),fileInput=ref<HTMLInputElement|null>(null),importing=ref(false)
const detail=ref<Report|null>(null),detailOpen=ref(false),detailLoading=ref(false),detailError=ref(''),publishing=ref(false)
const preparingBatch=ref(false),batchPublishing=ref(false),batchOpen=ref(false),batchError=ref(''),batchStart=ref(''),batchCandidates=ref<Report[]>([])
const batchPeriods=computed(()=>[...new Set(batchCandidates.value.map(report=>report.period_start))].map(start=>({value:start,label:periodLabel(batchCandidates.value.find(report=>report.period_start===start)!)})))
const batchReports=computed(()=>batchCandidates.value.filter(report=>report.period_start===batchStart.value))
const batchScoredCount=computed(()=>batchReports.value.filter(report=>report.score!==null).length)
const periodOptions=[{value:'week',label:'周评分'},{value:'month',label:'月评分'}]
const statusOptions=[{value:'',label:'当前结果'},{value:'draft',label:'待发布'},{value:'published',label:'已发布'},{value:'superseded',label:'历史版本'}]
const dimensionCodes = ['P','M','J','E']
const columns=computed(()=>[{key:'user_email',label:'成员'},{key:'period_start',label:'周期'},{key:'score',label:'总分',class:'text-right'},...(periodType.value==='month'?[{key:'rating',label:'月评级'}]:[]),...dimensionCodes.map(code=>({key:code,label:dimensionNames[code],class:'text-right'})),{key:'publication_status',label:'发布状态'},{key:'actions',label:'操作'}])
const runColumns=[{key:'source_name',label:'评测来源'},{key:'report_count',label:'报告份数',class:'text-right tabular-nums'},{key:'created_at',label:'接入时间'},{key:'digest',label:'结果校验值'}]
let request=0,detailRequest=0
async function load(){const current=++request;loading.value=true;error.value='';try{if(activeTab.value==='reports'){const data=await listReports(true,{period_type:periodType.value,period_start:periodStart.value,publication_status:status.value,search:search.value,page:page.value});if(current===request){reports.value=data.items;total.value=data.total}}else if(activeTab.value==='runs'){const data=await listRuns(page.value);if(current===request){runs.value=data.items;total.value=data.total}}}catch(e){if(current===request)error.value=(e as {message:string}).message}finally{if(current===request)loading.value=false}}
function beginReview(){switchTab('jobs');startOpen.value=true}
function showJobReports(email:string){search.value=email;periodStart.value='';status.value='';switchTab('reports')}
function resetList(){page.value=1;void load()}
function switchTab(tab:string){activeTab.value=tab;total.value=0;resetList()}
function changePage(delta:number){page.value+=delta;void load()}
async function openReport(id:number){const current=++detailRequest;detailOpen.value=true;detailLoading.value=true;detailError.value='';detail.value=null;try{const data=await getReport(true,id);if(current===detailRequest)detail.value=data}catch(e){if(current===detailRequest)detailError.value=(e as {message:string}).message}finally{if(current===detailRequest)detailLoading.value=false}}
async function importFile(event:Event){const input=event.target as HTMLInputElement;const file=input.files?.[0];if(!file)return;importing.value=true;try{if(file.size>16*1024*1024){app.showError('结果包不能超过16MB');return}let bundle:unknown;try{bundle=JSON.parse(await file.text())}catch{app.showError('请选择有效的JSON结果包');return}const run=await importResults(bundle);app.showSuccess(`已接入${run.report_count}份结果，等待确认发布`);await load()}catch(e){app.showError((e as {message:string}).message)}finally{input.value='';importing.value=false}}
async function publish(){if(!detail.value||publishing.value)return;publishing.value=true;try{detail.value=await publishReport(detail.value);app.showSuccess('已发布');await load()}catch(e){app.showError((e as {message:string}).message)}finally{publishing.value=false}}
async function prepareBatch(){
  if(preparingBatch.value||batchPublishing.value)return
  preparingBatch.value=true;batchError.value=''
  try{
    const filters={period_type:periodType.value,period_start:periodStart.value,publication_status:'draft',search:search.value,page:1,page_size:100}
    let data=await listReports(true,filters)
    if(!data.total){app.showError('当前查询条件下没有待发布结果');return}
    // 多周期草稿超过上限时，先缩到最近周期；其他周期可由日期筛选指定。
    if(data.total>100&&!filters.period_start)data=await listReports(true,{...filters,period_start:data.items[0].period_start})
    if(data.total>100){app.showError('单次最多发布100份结果，请通过账户查询缩小范围');return}
    batchCandidates.value=data.items;batchStart.value=data.items[0].period_start;batchOpen.value=true
  }catch(e){app.showError((e as {message:string}).message)}finally{preparingBatch.value=false}
}
function closeBatch(){if(!batchPublishing.value)batchOpen.value=false}
async function publishBatch(){
  if(batchPublishing.value||!batchReports.value.length)return
  batchPublishing.value=true;batchError.value=''
  const reports=batchReports.value
  try{
    const result=await batchPublishReports({period_type:reports[0].period_type,period_start:batchStart.value,reports:reports.map(report=>({id:report.id,revision:report.revision}))})
    batchOpen.value=false;app.showSuccess(`已发布${result.published_count}份结果`);await load()
  }catch(e){batchError.value=(e as {message:string}).message}finally{batchPublishing.value=false}
}
const formatTime=(s:string)=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
onMounted(load)
</script>
