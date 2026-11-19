import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AssessmentReader from './AssessmentReader.vue'
import type { Report, Item } from './api'

const item: Item = {
  item_code: 'P1', name: '目标清楚', state: 'observed', score: '80.0', exact: '80',
  reason: '明确了本轮交付对象。邻档判断：需说明哪些结果可以接受。', criterion: '', necessary_gap: false,
  components: null, period_states: null, limitations: [], evidence: [{
    id: 'e', source_ref: 'u', source_hash: 'h', task_title: '当日任务对话', occurred_at: '2026-11-05',
    role: 'user', origin: 'direct_expression', quote: '<script>用户原话</script>',
    context: '完整输入：<script>用户原话</script>', supports: '明确了本轮交付对象。', limitations: []
  }]
}
const report: Report = {
  id: 8, user_id: 8, user_email: 'person@example.test', revision: 1, period_type: 'week',
  period_start: '2026-11-05', period_end: '2026-11-05', publication_status: 'draft', published_at: null,
  created_at: '2026-11-05', score_status: 'scored', score: '78.3', exact: '783/10', rating: null,
  score_reason: '不应在评分与依据之间重复堆叠的总体评语。', policy_versions: { scale: 'test', aggregation: 'test', rating: 'test', rules: 'test' },
  dimensions: [{ code: 'P', name: '提示词质量', score: '80.0', exact: '80' }, { code: 'M', name: '任务驾驭度', score: null, exact: null }],
  items: [item, { ...item, item_code: 'M1', name: '方法分工', score: null, exact: null, state: 'unobservable', evidence: [], reason: '未形成独立方法判断。' }], limitations: []
}

describe('按维度阅读真实评分和案例', () => {
  it('部分维度的日总分说明归一权重，缺失维度仍显示横线', async () => {
    const wrapper = mount(AssessmentReader, { global: { stubs: { teleport: true } }, props: { report: { ...report, period_type: 'day' }, periodLabel: '2026-11-18' } })
    await wrapper.get('[aria-label="查看日总分计算方式"]').trigger('focusin')
    expect(wrapper.get('[role="tooltip"]').text()).toContain('日总分按已观察维度的原权重归一计算，未观察维度不补分。')
    expect(wrapper.findAll('.dimension-score strong').map(s => s.text())).toEqual(['80.0', '—'])
    await wrapper.setProps({ report })
    expect(wrapper.find('[aria-label="查看日总分计算方式"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('分数之后直接是依据，原话与AI评语分开且不执行原文HTML', () => {
    const wrapper = mount(AssessmentReader, { props: { report, periodLabel: '单日试评', sourceLabels: { u: '实际任务标题' } } })
    expect(wrapper.get('[aria-label="评分总览"]').element.nextElementSibling?.querySelector('h2')?.textContent).toBe('评分依据与任务解读')
    expect(wrapper.text()).not.toContain(report.score_reason)
    expect(wrapper.get('.source-excerpt').text()).toContain('实际任务标题')
    expect(wrapper.get('blockquote').text()).toBe(item.evidence![0].quote)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.get('.ai-assessment').text()).toContain('明确了本轮交付对象。')
    expect(wrapper.get('.ai-assessment').text()).toContain('距离下一档还差什么')
    expect(wrapper.get('details pre').text()).toBe(item.evidence![0].context)
    wrapper.unmount()
  })

  it('切换维度显示对应原判断，不把无分项补为70', async () => {
    const wrapper = mount(AssessmentReader, { props: { report, periodLabel: '单日试评' } })
    await wrapper.findAll('nav button')[1].trigger('click')
    expect(wrapper.get('.item-review').text()).toContain('未形成独立方法判断。')
    expect(wrapper.get('.item-review').text()).toContain('不单独计分')
    expect(wrapper.get('.item-review').text()).not.toContain('70')
    expect(wrapper.find('blockquote').exists()).toBe(false)
    expect(wrapper.findAll('nav button')[1].attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('工作流集中到任务详情，区分用户选择与AI执行，不改写评分', async () => {
    const wrapper = mount(AssessmentReader, { global: { stubs: { teleport: true } }, props: { report, periodLabel: '单日试评', sourceSessions: { u: 's' }, conversations: { s: {
      title: '插件交付', goal: '修复状态同步后提交', outcome: 'AI自述完成', workflow: [
        { name: 'push', purpose: '提交本次相关改动', selection: '用户主动指定 /push', user_refs: ['q1'], tool_refs: ['t1'] },
        { name: 'gary-ui', purpose: '检查界面规范', selection: 'AI自主加载', user_refs: [], tool_refs: ['t2'] },
      ]
    } } } })
    expect(wrapper.find('.task-goal').exists()).toBe(false)
    expect(wrapper.find('.task-workflow').exists()).toBe(false)
    await wrapper.get('.task-link').trigger('click')
    expect(wrapper.get('[role="dialog"] .task-goal').text()).toBe('修复状态同步后提交')
    expect(wrapper.get('.task-workflow').text()).toContain('用户主动指定 /push')
    expect(wrapper.get('.task-workflow').text()).toContain('AI自主加载')
    expect(wrapper.get('.item-heading').text()).toContain('80.0 分')
    expect(wrapper.get('blockquote').text()).toBe(item.evidence![0].quote)
    wrapper.unmount()
  })

  it('同一任务只展示一次标题，同条消息的连续引文紧凑合并，详情是真实相关对话', async () => {
    const evidence = item.evidence![0]
    const context = '先查询改动，说明原因。'
    const taskReport = { ...report, items: [{ ...item, evidence: [
      { ...evidence, quote: '先查询改动，', context },
      { ...evidence, id: 'e2', quote: '说明原因。', context },
      { ...evidence, id: 'e3', source_ref: 'u2', quote: '暂不修改。', context: '暂不修改。' }
    ] }] }
    const wrapper = mount(AssessmentReader, { global: { stubs: { teleport: true } }, props: { report: taskReport, periodLabel: '单日试评',
      sourceSessions: { u: 's', u2: 's' }, conversations: { s: {
        title: '改动查询', goal: '查清改动及原因', outcome: 'AI回复已定位', workflow: [], records: [
          { id: 'u', role: 'user', text: context, excerpt: false, source_order: 0, source_hash: 'h1', names: [] },
          { id: 'a', role: 'assistant', text: '已定位到登录逻辑，原因还需继续检查。', excerpt: false, source_order: 1, source_hash: 'h2', names: [] },
          { id: 't', role: 'tool', text: '真实工具原文', excerpt: true, source_order: 2, source_hash: 'h3', names: ['read-file'] },
          { id: 'u2', role: 'user', text: '暂不修改。', excerpt: false, source_order: 3, source_hash: 'h4', names: [] }
        ]
      } } } })
    expect(wrapper.findAll('.source-excerpt')).toHaveLength(1)
    expect(wrapper.findAll('blockquote')).toHaveLength(1)
    expect(wrapper.findAll('.quote-message').map(p => p.text())).toEqual([context, '暂不修改。'])
    expect(wrapper.text()).not.toContain('查看完整输入')
    expect(wrapper.find('.original-message').exists()).toBe(false)
    expect(wrapper.find('.context-records').exists()).toBe(false)
    await wrapper.get('.task-link').trigger('click')
    const dialogue = wrapper.get('[role="dialog"]')
    expect(dialogue.get('.modal-title').text()).toBe('改动查询')
    expect(dialogue.findAll('li > header').map(header => header.text())).toEqual(['用户本项引用', 'AI 回复', '工具记录原文节选', '用户本项引用'])
    expect(dialogue.text()).toContain('原因还需继续检查')
    expect(dialogue.text()).toContain('真实工具原文')
    wrapper.unmount()
  })

  it('跨分项共用一个任务详情，引用随入口切换，更换报告关闭旧内容', async () => {
    const evidence = item.evidence![0]
    const taskReport = { ...report, items: [item, {
      ...item, item_code: 'P2', name: '信息充分', evidence: [{ ...evidence, source_ref: 'u2', quote: '补充要求', context: '补充要求' }]
    }] }
    const wrapper = mount(AssessmentReader, { attachTo: document.body, global: { stubs: { teleport: true } }, props: {
      report: taskReport, periodLabel: '单日试评', sourceSessions: { u: 's', u2: 's' }, conversations: { s: {
        title: '共同任务', goal: '不应在多个分项重复的背景全文', outcome: '', workflow: [], records: [
          { id: 'u', role: 'user', text: evidence.context, excerpt: false, source_order: 0, source_hash: 'h', names: [] },
          { id: 'u2', role: 'user', text: '补充要求', excerpt: false, source_order: 1, source_hash: 'h2', names: [] }
        ]
      } }
    } })
    expect(wrapper.findAll('.task-link')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('不应在多个分项重复的背景全文')
    const links = wrapper.findAll('.task-link')
    ;(links[0].element as HTMLButtonElement).focus()
    await links[0].trigger('click')
    expect(wrapper.findAll('[role="dialog"]')).toHaveLength(1)
    expect(wrapper.findAll('.task-goal')).toHaveLength(1)
    expect(wrapper.get('.context-records li.cited').text()).toContain(evidence.context)
    await wrapper.get('[aria-label="Close modal"]').trigger('click')
    expect(document.activeElement).toBe(links[0].element)
    await links[1].trigger('click')
    expect(wrapper.findAll('[role="dialog"]')).toHaveLength(1)
    expect(wrapper.findAll('.context-records li.cited')).toHaveLength(1)
    expect(wrapper.get('.context-records li.cited').text()).toContain('补充要求')
    await wrapper.setProps({ report: { ...report, id: 9 } })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.body.classList.contains('modal-open')).toBe(false)
    wrapper.unmount()
  })

  it('只有同文或空白差异时不提供重复展开，也不把同名任务强行合并', () => {
    const evidence = { ...item.evidence![0], quote: '用户原话', context: ' 用户原话\n' }
    const wrapper = mount(AssessmentReader, { props: { report: { ...report, items: [{ ...item, evidence: [
      evidence, { ...evidence, id: 'e2', source_ref: 'different-source' }
    ] }] }, periodLabel: '单日试评' } })
    expect(wrapper.findAll('.source-excerpt')).toHaveLength(2)
    expect(wrapper.find('details').exists()).toBe(false)
    wrapper.unmount()
  })
})
