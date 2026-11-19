import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TaskReviewSection from './TaskReviewSection.vue'
import { getTaskCase, type Report, type TaskCase, type TaskCasePage, type TaskRecord, type TaskReview } from './api'
vi.mock('./api', async () => ({ ...await vi.importActual('./api'), getTaskCase: vi.fn() }))
const record = (id: string, text = `完整记录${id}`): TaskRecord => ({ id, source_hash: 'hash', text_hash: 'hash', occurred_at: null, role: 'assistant', origin: 'assistant_output', text, limitations: ['时间未知，未认作独立验收。'] })
const task: TaskCase = { id: 'case-a', task_key: 'task-a', title: '字段核对', goal: '核对字段及边界', outcome: '外部结果未确认', context_status: 'source_incomplete', record_count: 2, claims: [{ text: '本人要求核对字段。', item_codes: ['P1'], record_ids: ['r2'], limitations: ['未保存外部验收'], assessment: { kind: 'unresolved', reason: '不能把AI回复当验收。' } }], limitations: ['仅已保存记录。'] }
const review: TaskReview = { version: 'task-evidence-20261106.1', status: 'week_checked', task_keys: ['task-a'], checked_task_keys: ['task-a'], scope_note: '本轮保存范围', cases: [task], week_judgments: [{ item_code: 'P1', reason: '本周分档及更高档边界', task_keys: ['task-a'], case_ids: ['case-a'], limitations: ['更高档未证实'] }] }
const report = { id: 17, revision: 3, period_type: 'week', items: [{ item_code: 'P1', name: '目标清楚', score: '80.0', reason: '本人明确了目标。', evidence: [], criterion: '目标明确' }], task_review: review } as Report
const page = (current: number, records: TaskRecord[]): TaskCasePage => ({ ...task, records, page: current, page_size: 1, total: 2 })
const get = vi.mocked(getTaskCase)
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(node => node.text().includes(text))!
beforeEach(() => { vi.resetAllMocks() })
describe('正式任务解读', () => {
  it('同次生成的两种角色使用相同正文，连续子项合并且无分说明原因', () => {
    const generated = { ...report, items: [{ ...report.items[0], score: null, state: 'unobservable', reason: '记录未包含验收安排，本项未评分。' }],
      task_review: { ...review, status: 'generated', checked_task_keys: [], week_judgments: [],
        cases: [{ ...task, claims: [task.claims[0], { ...task.claims[0], text: '记录没有包含验收安排。' }] }] } } as Report
    const personal = mount(TaskReviewSection, { props: { report: generated } })
    const admin = mount(TaskReviewSection, { props: { report: generated, admin: true } })
    expect(personal.text()).toBe(admin.text())
    expect(personal.findAll('.claim-heading')).toHaveLength(1)
    expect(personal.text()).toContain('未评分原因')
    expect(personal.text()).toContain('记录未包含验收安排，本项未评分。')
    expect(personal.text()).not.toMatch(/已核查|待核查|待证明|待复核|不可观察/)
    expect(get).not.toHaveBeenCalled()
  })
  it('展示评分原因，案例状态不向用户暴露内部核查进度', async () => {
    const wrapper = mount(TaskReviewSection, { props: { report } })
    expect(wrapper.text()).toContain('本周分档及更高档边界')
    expect(wrapper.text()).toContain('不计分原因')
    expect(wrapper.text()).toContain('不能把AI回复当验收。')
    await wrapper.setProps({ report: { ...report, task_review: { ...review, status: 'case_checked', week_judgments: [] } } })
    expect(wrapper.text()).not.toMatch(/已核查|待核查|待证明|待复核/)
    expect(wrapper.text()).not.toContain('本周分档及更高档边界')
    expect(get).not.toHaveBeenCalled()
  })
  it('不将initial/source_linked或未知版本当正式说明', () => {
    for (const value of [{ ...review, status: 'initial' }, { ...review, status: 'source_linked' }, { ...review, version: 'unknown' }]) {
      const wrapper = mount(TaskReviewSection, { props: { report: { ...report, task_review: value as TaskReview } } })
      expect(wrapper.find('section').exists()).toBe(false)
      wrapper.unmount()
    }
    expect(get).not.toHaveBeenCalled()
  })
  it('已知历史来源沿用真实理由，不显示核查状态或冒充任务识别', () => {
    const linked = { ...report, task_review: { ...review, version: 'source-evidence-20261116.1', status: 'source_linked', checked_task_keys: [], week_judgments: [], cases: [{ ...task, claims: [{ ...task.claims[0], assessment: undefined }] }] } } as Report
    const wrapper = mount(TaskReviewSection, { props: { report: linked } })
    expect(wrapper.text()).toContain('评分依据与任务解读')
    expect(wrapper.text()).toContain('关联来源 1 组')
    expect(wrapper.text()).toContain('本人明确了目标。')
    expect(wrapper.text()).not.toMatch(/已核查|待核查|待证明|待复核|本周识别/)
    expect(get).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('逐页找到完整引用，保留AI/未知时间/限制，HTML只按文字显示', async () => {
    get.mockResolvedValueOnce(page(1, [record('r1')])).mockResolvedValueOnce(page(2, [record('r2', '<img src=x onerror=alert(1)>AI完整回复')]))
    const wrapper = mount(TaskReviewSection, { props: { report } })
    await button(wrapper, '对应记录').trigger('click'); await flushPromises()
    expect(get.mock.calls).toEqual([[false, 17, 'case-a', 1], [false, 17, 'case-a', 2]])
    expect(wrapper.text()).toContain('AI 回复')
    expect(wrapper.text()).toContain('时间未知')
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>AI完整回复')
    expect(wrapper.find('img').exists()).toBe(false)
    await button(wrapper, '收起原文').trigger('click'); await button(wrapper, '对应记录').trigger('click'); await flushPromises()
    expect(get).toHaveBeenCalledTimes(2)
  })
  it('管理员失败保留真实错误，重试同页且不自动重发', async () => {
    get.mockRejectedValueOnce(new Error('本次请求无访问权限')).mockResolvedValueOnce(page(1, [record('r1'), record('r2')]))
    const wrapper = mount(TaskReviewSection, { props: { report, admin: true } })
    await button(wrapper, '对应记录').trigger('click'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('本次请求无访问权限')
    expect(get).toHaveBeenCalledTimes(1)
    await button(wrapper, '重试').trigger('click'); await flushPromises()
    expect(get.mock.calls).toEqual([[true, 17, 'case-a', 1], [true, 17, 'case-a', 1]])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })
  it('切换案例隔离旧响应，并发点击不重复发页', async () => {
    let resolve!: (value: TaskCasePage) => void
    get.mockReturnValueOnce(new Promise(done => { resolve = done }))
    const wrapper = mount(TaskReviewSection, { props: { report: { ...report, task_review: { ...review, cases: [task, { ...task, id: 'case-b', title: '第二个案例' }] } } } })
    await button(wrapper, '对应记录').trigger('click'); await button(wrapper, '收起原文').trigger('click'); await button(wrapper, '对应记录').trigger('click')
    expect(get).toHaveBeenCalledTimes(1)
    await button(wrapper, '第二个案例').trigger('click')
    resolve(page(1, [record('r2', '前一案例旧请求')])); await flushPromises()
    expect(wrapper.text()).not.toContain('前一案例旧请求')
    expect(wrapper.get('article').attributes('aria-label')).toBe('第二个案例')
  })
  it('引用已齐后完整对话后页失败，重试仍能恢复同一页', async () => {
    get.mockResolvedValueOnce({ ...page(1, [record('r2')]), total: 2 })
      .mockRejectedValueOnce(new Error('后页读取暂时失败'))
      .mockResolvedValueOnce(page(2, [record('r3')]))
    const wrapper = mount(TaskReviewSection, { props: { report } })
    await button(wrapper, '对应记录').trigger('click'); await flushPromises()
    expect(get).toHaveBeenCalledTimes(1)
    const details = wrapper.findAll('details').find(node => node.text().includes('查看任务对话'))!
    ;(details.element as HTMLDetailsElement).open = true
    await details.trigger('toggle'); await flushPromises()
    await button(wrapper, '继续加载').trigger('click'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('后页读取暂时失败')
    expect(wrapper.text()).toContain('完整记录r2')
    expect(get).toHaveBeenCalledTimes(2)
    await button(wrapper, '重试').trigger('click'); await flushPromises()
    expect(get.mock.calls).toEqual([[false, 17, 'case-a', 1], [false, 17, 'case-a', 2], [false, 17, 'case-a', 2]])
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('完整记录r3')
    expect(wrapper.text()).toContain('已展示 2 / 2 条记录')
  })
  it('完整对话按需分页，缺少引用如实提示', async () => {
    get.mockResolvedValueOnce({ ...page(1, [record('r1')]), total: 1 })
    const wrapper = mount(TaskReviewSection, { props: { report } })
    const details = wrapper.findAll('details').find(node => node.text().includes('查看任务对话'))!
    ;(details.element as HTMLDetailsElement).open = true
    await details.trigger('toggle'); await flushPromises()
    expect(wrapper.text()).toContain('完整记录r1')
    await button(wrapper, '对应记录').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('部分对应记录未返回，当前无法完整展示。')
    expect(get).toHaveBeenCalledTimes(1)
  })
})
