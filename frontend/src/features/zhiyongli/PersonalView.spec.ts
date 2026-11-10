import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PersonalView from './PersonalView.vue'
import { listReports } from './api'

vi.mock('./api', () => ({ listReports: vi.fn(), getReport: vi.fn(), periodLabel: vi.fn() }))

describe('首次上线的个人空状态', () => {
  beforeEach(() => { vi.mocked(listReports).mockReset() })
  const mountPage = () => mount(PersonalView, { global: { stubs: {
    AppLayout: { template: '<main><slot /></main>' }, RouterLink: true, Select: true,
    LoadingSpinner: true, ReportBody: true,
    EmptyState: { props: ['title', 'description'], template: '<section>{{ title }} {{ description }}</section>' }
  } } })

  it('真实空列表只显示待发布状态，不展示示例分数或雷达图', async () => {
    vi.mocked(listReports).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, pages: 0 })
    const page = mountPage()
    await flushPromises()
    expect(page.text()).toContain('暂无已发布的周评分')
    expect(page.text()).toContain('评分完成并由管理员发布后')
    expect(page.find('report-body-stub').exists()).toBe(false)
    expect(page.text()).not.toContain('数据采集中')
    page.unmount()
  })

  it('接口失败展示实际错误和重试，不伪装无评分', async () => {
    vi.mocked(listReports).mockRejectedValue(new Error('访问被拒绝'))
    const page = mountPage()
    await flushPromises()
    expect(page.text()).toContain('访问被拒绝')
    expect(page.text()).toContain('重试')
    expect(page.text()).not.toContain('暂无已发布')
    page.unmount()
  })
})
