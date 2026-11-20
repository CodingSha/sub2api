import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import AdminView from './AdminView.vue'
import ReviewJobs from './ReviewJobs.vue'
import { getReport, listReports, startDayRecheck, type Report } from './api'

vi.mock('./api', async () => ({
  ...(await vi.importActual('./api')), listReports: vi.fn(), getReport: vi.fn(), startDayRecheck: vi.fn()
}))
vi.mock('@/stores/app', () => ({ useAppStore: () => ({ showSuccess: vi.fn() }) }))

it('管理员确认后才复评，同一报告错误如实展示，并从进度打开确切草稿', async () => {
  const report = { id: 19, user_email: 'person@example.test', period_type: 'day', period_start: '2026-11-18', publication_status: 'published', policy_versions: { rules: '1.6' } } as Report
  const draft = { ...report, id: 21, publication_status: 'draft' } as Report
  vi.mocked(listReports).mockResolvedValue({ items: [report], total: 1, page: 1, pages: 1, page_size: 20 })
  vi.mocked(getReport).mockResolvedValueOnce(report).mockResolvedValueOnce(draft)
  vi.mocked(startDayRecheck).mockRejectedValueOnce(new Error('该账户不在当前日评范围内'))
  vi.mocked(startDayRecheck).mockResolvedValueOnce({ id: 37, user_email: report.user_email, result_report_id: null } as Awaited<ReturnType<typeof startDayRecheck>>)
  const wrapper = mount(AdminView, { global: { plugins: [createI18n({ legacy: false, locale: 'zh', messages: { zh: {} } })], stubs: {
    AppLayout: { template: '<div><slot /></div>' }, RouterLink: true, Select: true, ReportBody: true,
    BaseDialog: { props: ['show'], template: '<div v-if="show"><slot/><slot name="footer"/></div>' },
    DataTable: { props: ['data'], template: '<div><div v-for="row in data" :key="row.id"><slot name="cell-actions" :row="row"/></div></div>' },
    ReviewJobs: { props: ['initialSearch'], template: '<div>{{ initialSearch }}</div>' }
  } } })
  const click = (label: string) => wrapper.findAll('button').find(button => button.text() === label)!.trigger('click')
  await flushPromises()
  await click('查看'); await flushPromises()
  await click('重新采集并评审')
  expect(startDayRecheck).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('不会自动替换已发布评分')
  await click('确认复评'); await flushPromises()
  expect(startDayRecheck).toHaveBeenCalledWith(report.id)
  expect(wrapper.get('[role="alert"]').text()).toBe('该账户不在当前日评范围内')
  await click('确认复评'); await flushPromises()
  const jobs = wrapper.getComponent(ReviewJobs)
  expect(jobs.props('initialSearch')).toBe(report.user_email)
  jobs.vm.$emit('reports', { recheck_report_id: report.id, result_report_id: draft.id })
  await flushPromises()
  expect(getReport).toHaveBeenLastCalledWith(true, draft.id)
  expect(wrapper.text()).toContain('待发布')
  wrapper.unmount()
})
