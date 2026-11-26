import { defineComponent } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import PersonalView from './PersonalView.vue'
import { listReports, getReport } from './api'
import type { Report } from './api'
vi.mock('./api', async () => ({ ...(await vi.importActual('./api')), listReports: vi.fn(), getReport: vi.fn() }))
const brief = (id: number, date: string) => ({ id, period_type: 'day', period_start: date, period_end: '2026-11-19', score: `${id}.0`, policy_versions: { rules: '1.6' } } as Report)
const Select = defineComponent({ props: ['modelValue', 'options'], emits: ['update:modelValue', 'change'], template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', Number($event.target.value)); $emit(\'change\')"><option v-for="option in options" :value="option.value">{{ option.label }}</option></select>' })
const global = { stubs: { AppLayout: { template: '<div><slot /></div>' }, RouterLink: true, EmptyState: { props: ['title'], template: '<div>{{ title }}</div>' }, LoadingSpinner: true, ReportBody: { props: ['report'], template: '<article>{{ report.period_start }}：{{ report.score }}</article>' }, Select } }
beforeEach(() => vi.resetAllMocks())
describe('每日评分日期切换', () => {
  it('默认请求周评分，切到每日分析后可更换整份报告', async () => {
    const reports = [brief(18, '2026-11-18'), brief(17, '2026-11-17')]
    vi.mocked(listReports).mockResolvedValue({ items: reports, total: 2, page: 1, pages: 1, page_size: 20 })
    vi.mocked(getReport).mockImplementation(async (_admin, id) => reports.find(r => r.id === id)!)
    const wrapper = mount(PersonalView, { global }); await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '每日分析')!.trigger('click'); await flushPromises()
    expect(listReports).toHaveBeenNthCalledWith(1, false, { period_type: 'week', page: 1 })
    expect(listReports).toHaveBeenLastCalledWith(false, { period_type: 'day', page: 1 })
    const dates = wrapper.get('select[aria-label="选择评分日期"]')
    expect(dates.findAll('option').map(o => o.text())).toEqual(['2026-11-18', '2026-11-17'])
    expect(wrapper.get('article').text()).toBe('2026-11-18：18.0')
    await dates.setValue('17'); await flushPromises()
    expect(wrapper.get('article').text()).toBe('2026-11-17：17.0'); wrapper.unmount()
  })
  it('快速切换时较早请求不得覆盖最新日期', async () => {
    const reports = [brief(18, '2026-11-18'), brief(17, '2026-11-17')]
    vi.mocked(listReports).mockResolvedValue({ items: reports, total: 2, page: 1, pages: 1, page_size: 20 })
    let resolveOld!: (r: Report) => void
    vi.mocked(getReport).mockImplementation((_admin, id) => id === 18 ? new Promise(resolve => { resolveOld = resolve }) : Promise.resolve(reports[1]))
    const wrapper = mount(PersonalView, { global }); await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '每日分析')!.trigger('click'); await flushPromises()
    await wrapper.get('select[aria-label="选择评分日期"]').setValue('17'); await flushPromises()
    expect(wrapper.get('article').text()).toContain('2026-11-17')
    resolveOld(reports[0]); await flushPromises()
    expect(wrapper.get('article').text()).toContain('2026-11-17'); wrapper.unmount()
  })
})
