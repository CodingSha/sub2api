import { apiClient } from '@/api/client'

export interface Evidence {
  id: string
  source_ref: string
  source_hash: string
  task_title: string
  occurred_at: string
  role: 'user' | 'assistant' | 'tool' | 'context'
  origin: string
  quote: string
  supports: string
  context: string
  limitations: string[] | null
}
export interface Item {
  item_code: string
  name: string
  state: string
  exact: string | null
  score: string | null
  reason: string
  criterion: string
  necessary_gap: boolean
  components: {period_start:string;period_end:string;exact:string;necessary_gap:boolean}[] | null
  period_states: Record<string,string> | null
  evidence: Evidence[] | null
  limitations: string[] | null
}
export interface Dimension { code: string; name: string; exact: string | null; score: string | null }
export interface TaskRecord {
  id: string
  source_hash: string
  text_hash: string
  occurred_at: string | null
  role: 'user' | 'assistant' | 'tool_call' | 'tool_result' | 'context'
  origin: 'direct_expression' | 'mixed_material' | 'assistant_output' | 'recorded_tool' | 'unattributed'
  text: string
  limitations: string[] | null
}
export interface TaskClaim {
  text: string
  item_codes: string[] | null
  record_ids: string[]
  limitations: string[] | null
  assessment?: { kind: 'support' | 'shortfall' | 'unresolved'; reason: string }
}
export interface TaskCase {
  id: string
  task_key: string
  title: string
  goal: string
  outcome: string
  context_status: 'available_records' | 'source_incomplete'
  record_count: number
  claims: TaskClaim[]
  limitations: string[] | null
}
export interface TaskCasePage extends TaskCase { records: TaskRecord[]; page: number; page_size: number; total: number }
export interface TaskReview {
  version: string
  status: 'initial' | 'source_linked' | 'case_checked' | 'week_checked' | 'generated'
  task_keys: string[]
  checked_task_keys: string[]
  scope_note: string
  cases: TaskCase[]
  week_judgments: { item_code: string; reason: string; task_keys: string[]; case_ids: string[] | null; limitations: string[] | null }[] | null
}
export interface WorkdayScope {
  version: string; registry_sha256: string; calendar_sha256: string
  period_start: string; period_end: string; working_dates: string[]
  evaluation_dates?: string[]
  observed_until: string; freeze_at: string; candidate_due_at: string | null
}
export interface DailyReview {
  version: string
  model: string
  request_digest: string
  window_start: string
  window_end: string
  source_labels: Record<string, string>
  source_sessions: Record<string, string>
  behaviors: Record<string, { primary_item: string; source_ref: string; claim: string }>
  conversations: Record<string, import('./AssessmentReader.vue').AssessmentTask>
}
export interface Report {
  daily_review?: DailyReview
  id: number
  user_id: number
  user_email: string
  revision: number
  period_type: 'day' | 'week' | 'month'
  period_start: string
  period_end: string
  publication_status: 'draft' | 'published' | 'superseded'
  published_at: string | null
  score_status: 'scored' | 'no_activity' | 'insufficient_evidence' | 'observation_only' | 'data_pending'
  score: string | null
  exact: string | null
  score_reason: string
  score_explanation?: { reasons: string[]; effect: string }
  rating: string | null
  policy_versions: { scale: string; aggregation: string; rating: string; rules: string; review?: string }
  dimensions: Dimension[]
  items: Item[] | null
  limitations: string[] | null
  created_at: string
  workday_scope?: WorkdayScope
  task_review?: TaskReview
  sample_initial?: {
    version: string; sample_digest: string; audit_status: 'not_reviewed'
    method: 'observed_dimension_weights'; exact: string | null; score: string | null
    covered_weight: number; missing_dimensions: string[]
  }
  eligibility?: {
    version: string
    eligible: boolean
    route: string
    reasons: string[]
    task_count: number
    active_days: number
    active_weeks: number
    valid_weeks?: number
    total_weeks?: number
    sources_complete: boolean
    dimension_coverage: { code: string; events: number; tasks: number; days: number; weeks: number }[]
  }
  assessment?: {
    version: string
    source_gap_digest: string
    scope: string
    issues: { id: string; status: 'limitation' | 'blocking'; description: string; reason: string }[]
  }
}
export interface Run { id: number; digest: string; source_name: string; report_count: number; created_at: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number; pages: number }
export interface Filters { period_type?: string; period_start?: string; publication_status?: string; search?: string; page?: number; page_size?: number }

const base = (admin: boolean) => admin ? '/admin/zhiyongli' : '/zhiyongli/me'
export async function listReports(admin: boolean, params: Filters) {
  return (await apiClient.get<Page<Report>>(`${base(admin)}/reports`, { params })).data
}
export async function getReport(admin: boolean, id: number) {
  return (await apiClient.get<Report>(`${base(admin)}/reports/${id}`)).data
}
export async function getTaskCase(admin: boolean, reportId: number, caseId: string, page = 1) {
  return (await apiClient.get<TaskCasePage>(`${base(admin)}/reports/${reportId}/task-cases/${encodeURIComponent(caseId)}`, { params: { page, page_size: 50 } })).data
}
export async function publishReport(report: Report) {
  return (await apiClient.post<Report>(`/admin/zhiyongli/reports/${report.id}/publish`, { revision: report.revision })).data
}
export async function batchPublishReports(input: {period_type: string; period_start: string; reports: {id: number; revision: number}[]}) {
  return (await apiClient.post<{published_count: number; report_ids: number[]}>('/admin/zhiyongli/reports/batch-publish', input)).data
}
export async function importResults(bundle: unknown) {
  return (await apiClient.post<Run>('/admin/zhiyongli/imports', bundle)).data
}
export async function listRuns(page: number) {
  return (await apiClient.get<Page<Run>>('/admin/zhiyongli/runs', { params: { page, page_size: 20 } })).data
}
export interface ReviewJob {
  id: number; user_id: number; user_email: string
  period_type: 'day' | 'week' | 'month'; period_start: string; period_end: string; rules_version: string
  state: 'queued' | 'running' | 'needs_review' | 'unknown' | 'failed' | 'completed' | 'no_activity'
  active: boolean
  phase: string; completed_units: number; total_units: number; message: string
  created_at: string; updated_at: string
  scope_key: string; workday_scope?: WorkdayScope
  recheck_report_id: number | null; result_report_id: number | null
}
export async function startDayRecheck(reportId: number) {
  return (await apiClient.post<ReviewJob>(`/admin/zhiyongli/reports/${reportId}/recheck`)).data
}
export async function startJobs(input: {user_ids: number[]; period_type: 'day' | 'week' | 'month'; period_start: string}) {
  return (await apiClient.post<ReviewJob[]>('/admin/zhiyongli/jobs', input)).data
}
// 全员周评审：周期（最近已封口工作周）和参评人员由服务端确定，不传参数。
export async function startLastWeekJobs() {
  return (await apiClient.post<ReviewJob[]>('/admin/zhiyongli/jobs/last-week')).data
}
export async function listJobs(page: number, filters: {state?: string; search?: string} = {}) {
  return (await apiClient.get<Page<ReviewJob>>('/admin/zhiyongli/jobs', {params:{page,page_size:20,...filters}})).data
}
export async function resumeJob(id: number) {
  return (await apiClient.post<ReviewJob>(`/admin/zhiyongli/jobs/${id}/resume`)).data
}
export const statusText = { draft: '待发布', published: '已发布', superseded: '历史版本' }
export const stateText: Record<string, string> = {
  observed: '已评分', no_opportunity: '无处理机会', unobservable: '未评分',
  attribution_unclear: '未评分', pending: '未评分', no_activity: '本期无可评记录', insufficient_evidence: '证据不足',
  related: '关联其他分项', not_applicable: '不适用', mixed_unscored: '本月无可评数值',
  observation_only: '观察不足，保留分维评价', data_pending: '数据或评测待完成',
  excluded_week: '未计入月分（无正式周评）'
}
export const periodLabel = (r: Report) => r.period_type === 'day' ? r.period_start : `${r.period_start} — ${new Date(new Date(`${r.period_end}T00:00:00Z`).getTime() - 86400000).toISOString().slice(0, 10)}`
