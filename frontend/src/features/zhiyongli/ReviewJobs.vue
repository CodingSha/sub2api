<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between text-sm">
      <span class="text-gray-500 dark:text-gray-400">{{ total }} 项评审</span>
      <button class="btn btn-secondary" :disabled="loading" @click="load()">刷新</button>
    </div>
    <p v-if="error" role="alert" class="text-sm text-red-600">{{ error }}</p>
    <DataTable :columns="columns" :data="jobs" :loading="loading">
      <template #cell-period_start="{row}">
        <span class="tabular-nums">{{ row.period_start }} · {{ row.period_type === 'week' ? '周' : '月' }} · {{ row.rules_version }}</span>
        <WorkdayRange v-if="row.workday_scope" :scope="row.workday_scope" class="mt-1 w-56" />
        <span v-else class="block text-xs text-gray-500">{{ row.period_type === 'week' ? '历史自然周范围' : '历史自然月范围' }}</span>
      </template>
      <template #cell-state="{row}"><span class="badge" :class="row.state === 'completed' ? 'badge-success' : 'badge-gray'">{{ states[row.state as ReviewJob['state']] }}</span></template>
      <template #cell-phase="{row}">
        <div class="text-sm">{{ phases[row.phase] || row.phase }}<span v-if="row.total_units > 0" class="ml-2 tabular-nums text-gray-500">{{ row.completed_units }}/{{ row.total_units }}</span></div>
        <div v-if="row.message" class="max-w-xs whitespace-normal text-xs leading-5 text-gray-500 dark:text-gray-400">{{ row.message }}</div>
      </template>
      <template #cell-updated_at="{value}"><span class="tabular-nums text-xs">{{ formatTime(value) }}</span></template>
      <template #cell-actions="{row}">
        <button v-if="row.state === 'failed' || row.state === 'needs_review'" class="text-sm text-primary-700 dark:text-primary-400" :disabled="resuming === row.id" @click="resume(row)">{{ resuming === row.id ? '恢复中…' : '恢复校验' }}</button>
        <button v-else-if="row.state === 'completed'" class="text-sm text-primary-700 dark:text-primary-400" @click="$emit('reports', row.user_email)">查看结果</button>
        <span v-else class="text-gray-400">—</span>
      </template>
      <template #empty><p class="text-sm">暂无评审任务</p></template>
    </DataTable>
    <div v-if="total > 20" class="flex items-center justify-end gap-3 text-sm">
      <button class="btn btn-secondary" :disabled="page === 1 || loading" @click="changePage(-1)">上一页</button>
      <span>{{ page }} / {{ Math.ceil(total/20) }}</span>
      <button class="btn btn-secondary" :disabled="page*20 >= total || loading" @click="changePage(1)">下一页</button>
    </div>
    <BaseDialog :show="startOpen" title="开始评审" width="normal" :close-on-escape="!starting" :show-close-button="!starting" @close="$emit('close')">
      <form id="start-review" class="space-y-4" @submit.prevent="start">
        <div class="grid grid-cols-2 gap-4">
          <label class="text-sm">周期类型<Select v-model="kind" :options="periods" class="mt-2" aria-label="评审周期类型" /></label>
          <label class="text-sm">开始日期<input v-model="startDate" type="date" class="input mt-2" aria-label="评审开始日期" required /></label>
        </div>
        <p class="text-xs text-gray-500">{{ kind === 'week' ? '选择已封口工作周的周一。' : '选择月份首日；最后相交工作周封口后可评审。' }}按法定节假日、调休及公司例外确定计分日期，候选生成后需确认发布。</p>
        <div class="flex gap-2"><input v-model="userSearch" class="input" placeholder="查找账户邮箱" aria-label="查找评审账户" @keydown.enter.prevent="findUsers" /><button type="button" class="btn btn-secondary shrink-0" :disabled="finding" @click="findUsers">查询</button></div>
        <div v-if="selected.length" class="flex flex-wrap gap-2">
          <button v-for="user in selected" :key="user.id" type="button" class="text-xs text-primary-700 underline dark:text-primary-400" :aria-label="`取消选择${user.email}`" @click="toggle(user)">{{ user.email }} ×</button>
        </div>
        <fieldset class="max-h-52 space-y-2 overflow-y-auto border-y border-gray-200 py-3 dark:border-dark-700">
          <legend class="text-sm">评审人员（{{ selected.length }}/20）</legend>
          <p v-if="finding" class="text-sm text-gray-500">查询中…</p>
          <label v-for="user in users" :key="user.id" class="flex min-h-8 items-center gap-2 text-sm"><input type="checkbox" :checked="selected.some(u => u.id === user.id)" :disabled="starting || selected.length >= 20 && !selected.some(u => u.id === user.id)" @change="toggle(user)" />{{ user.email }}</label>
          <p v-if="!finding && !users.length" class="text-sm text-gray-500">暂无匹配账户</p>
        </fieldset>
        <p v-if="userTotal > 20" class="text-xs text-gray-500">匹配{{ userTotal }}个账户，显示前20个；可通过邮箱缩小范围。</p>
        <p v-if="startError" role="alert" class="text-sm text-red-600">{{ startError }}</p>
      </form>
      <template #footer><div class="flex justify-end gap-3"><button class="btn btn-secondary" :disabled="starting" @click="$emit('close')">取消</button><button form="start-review" class="btn btn-primary" :disabled="starting || !selected.length || !startDate">{{ starting ? '登记中…' : '开始评审' }}</button></div></template>
    </BaseDialog>
  </div>
</template>
<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import DataTable from '@/components/common/DataTable.vue'
import BaseDialog from '@/components/common/BaseDialog.vue'
import Select from '@/components/common/Select.vue'
import WorkdayRange from './WorkdayRange.vue'
import { list as listUsers } from '@/api/admin/users'
import { useAppStore } from '@/stores/app'
import { listJobs, startJobs, resumeJob, type ReviewJob } from './api'
const props = defineProps<{startOpen:boolean}>()
const emit = defineEmits<{close:[];reports:[email:string]}>()
const app=useAppStore()
const jobs=ref<ReviewJob[]>([]),page=ref(1),total=ref(0),loading=ref(false),error=ref(''),resuming=ref<number|null>(null)
const kind=ref<'week'|'month'>('week'),startDate=ref(''),starting=ref(false),startError=ref('')
const users=ref<{id:number;email:string}[]>([]),selected=ref<{id:number;email:string}[]>([]),userSearch=ref(''),userTotal=ref(0),finding=ref(false)
const periods=[{value:'week',label:'周评分'},{value:'month',label:'月评分'}]
const states:Record<ReviewJob['state'],string>={queued:'等待执行',running:'评审中',needs_review:'待复核',unknown:'请求结果不明',failed:'执行失败',completed:'候选已生成',no_activity:'本期无记录'}
const phases:Record<string,string>={collecting:'采集来源',reading:'阅读来源',identifying:'识别目标',reviewing:'分项评审',checking:'核对依据',importing:'接入候选',done:'已完成',paused:'已暂停'}
const columns=[{key:'user_email',label:'成员'},{key:'period_start',label:'周期 / 规则'},{key:'state',label:'状态'},{key:'phase',label:'进度与问题'},{key:'updated_at',label:'最近更新'},{key:'actions',label:'操作'}]
let request=0,findRequest=0,alive=true,timer:ReturnType<typeof setTimeout>|undefined
async function load(silent=false){const current=++request;if(!silent)loading.value=true;try{const value=await listJobs(page.value);if(alive&&current===request){jobs.value=value.items;total.value=value.total;error.value=''}}catch(e){if(alive&&current===request)error.value=(e as Error).message}finally{if(current===request)loading.value=false}}
async function poll(){await load(true);if(alive)timer=setTimeout(poll,5000)}
function changePage(delta:number){page.value+=delta;void load()}
async function findUsers(){const current=++findRequest;finding.value=true;try{const result=await listUsers(1,20,{search:userSearch.value});if(alive&&current===findRequest){users.value=result.items;userTotal.value=result.total}}catch(e){if(alive&&current===findRequest)startError.value=(e as Error).message}finally{if(current===findRequest)finding.value=false}}
function toggle(user:{id:number;email:string}){if(starting.value)return;const index=selected.value.findIndex(u=>u.id===user.id);if(index>=0)selected.value.splice(index,1);else if(selected.value.length<20)selected.value.push(user)}
async function start(){if(starting.value||!selected.value.length)return;starting.value=true;startError.value='';try{await startJobs({user_ids:selected.value.map(u=>u.id),period_type:kind.value,period_start:startDate.value});emit('close');page.value=1;await load()}catch(e){startError.value=(e as Error).message}finally{starting.value=false}}
async function resume(job:ReviewJob){resuming.value=job.id;try{await resumeJob(job.id);await load()}catch(e){app.showError((e as Error).message)}finally{resuming.value=null}}
const formatTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})
watch(()=>props.startOpen,open=>{if(open){startError.value='';void findUsers()}},{immediate:true})
onMounted(()=>{void poll()})
onUnmounted(()=>{alive=false;request++;findRequest++;if(timer)clearTimeout(timer)})
</script>
