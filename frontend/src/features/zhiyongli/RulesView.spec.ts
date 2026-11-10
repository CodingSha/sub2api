import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import RulesView from './RulesView.vue'
import rules16 from '../../../../docs/ZHIYONGLI_SCORING_RULES_V1_6.md?raw'

const route = reactive<{ query: { version?: string } }>({ query: {} })
vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: false }) }))
let downloaded: Blob
const createUrl = vi.fn((blob: Blob) => { downloaded = blob; return 'blob:rules' })
const revokeUrl = vi.fn()
const page = () => mount(RulesView, { global: { stubs: {
  AppLayout: { template: '<main><slot /></main>' },
  RouterLink: { template: '<a><slot /></a>' }
} } })

beforeEach(() => {
  route.query = {}
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('版本化评分规则', () => {
  it('默认显示当前规则，章节与下载均对应同一正文', async () => {
    const wrapper = page()
    expect(wrapper.text()).toContain('当前规则 · v1.6')
    expect(wrapper.text()).toContain('不设识别出几个任务才能评分的门槛')
    const nav = wrapper.get('nav[aria-label="规则章节"]')
    for (const link of nav.findAll('a')) {
      expect(wrapper.find(link.attributes('href')).exists()).toBe(true)
    }
    const download = wrapper.get('a[download]')
    expect(download.attributes('download')).toBe('智用力评分规则-v1.6.md')
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.readAsText(downloaded)
    })
    expect(text).toBe(rules16)
    wrapper.unmount()
    expect(revokeUrl).toHaveBeenCalled()
  })

  it('历史报告显示自己的规则，切换与未知版本不回退成最新版', async () => {
    route.query = { version: '1.4' }
    const wrapper = page()
    expect(wrapper.text()).toContain('历史规则 · v1.4')
    expect(wrapper.text()).toContain('查看当前规则 v1.6')
    expect(wrapper.get('a[download]').attributes('download')).toContain('v1.4')
    route.query = { version: '1.5' }
    await flushPromises()
    expect(wrapper.text()).toContain('历史规则 · v1.5')
    route.query = { version: '9.9' }
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('9.9')
    expect(wrapper.find('article').exists()).toBe(false)
    expect(wrapper.find('a[download]').exists()).toBe(false)
    wrapper.unmount()
  })
})
