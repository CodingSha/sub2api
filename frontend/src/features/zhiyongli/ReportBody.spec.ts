import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ReportBody from './ReportBody.vue'
import type { Evidence, Report } from './api'

const record: Evidence = {
  id: 'checked', source_ref: 'source:event', source_hash: 'hash',
  task_title: '页面修订', occurred_at: '2026-09-07T08:00:00Z', role: 'user',
  origin: 'direct_expression', quote: '仅调整本次改动的页面说明。',
  context: '仅调整本次改动的页面说明。', supports: '覆盖两个任务的整项综合归纳。', limitations: []
}
const report: Report = {
  id: 1, user_id: 1, user_email: 'review@example.test', revision: 1,
  period_type: 'week', period_start: '2026-09-07', period_end: '2026-09-14',
  publication_status: 'published', published_at: null, created_at: '2026-09-14T00:00:00Z',
  score_status: 'scored', score: '80.0', exact: '80', rating: null, score_reason: '',
  policy_versions: { scale: 'test', aggregation: 'test', rating: 'test', rules: '1.3' },
  dimensions: [], limitations: ['内部采集审查记录。'],
  items: [{ item_code: 'P1', name: '目标清楚', state: 'observed', exact: '80', score: '80.0',
    reason: '整项原始评审理由。', criterion: '目标及范围明确。', necessary_gap: false,
    components: [], period_states: null, limitations: [], evidence: [record,
      { ...record, id: 'limited', task_title: '待核汇报稿', quote: '受限的引用。',
        context: '受限的引用。', limitations: ['来源事件存在截断，仅使用已保存的可核部分。'] }
    ]
  }]
}

describe('报告的用户依据与管理员评审记录', () => {
  it('正式日评使用新阅读布局，分数后直接展示依据和任务上下文入口', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report, period_type: 'day', period_start: '2026-11-18', period_end: '2026-11-19', daily_review: {
      version: 'daily-task-review-20261120.2', model: 'deepseek-v4.1-flash-local', request_digest: 'a', behaviors: {},
      window_start: '2026-11-18T08:00:00+08:00', window_end: '2026-11-18T18:00:00+08:00',
      source_labels: { 'person:message-1': '页面说明调整' }, source_sessions: {}, conversations: {}
    } } } })
    expect(wrapper.find('.assessment-reader').exists()).toBe(true)
    expect(wrapper.get('[aria-label="评分总览"]').element.nextElementSibling?.querySelector('h2')?.textContent).toBe('评分依据与任务解读')
    expect(wrapper.find('.score-hero').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('分项评分与标准')
    wrapper.unmount()
  })

  it('单日试评明确标识，评分理由只展示一次并展开下一档差距', () => {
    const basis = '能说明本次交付目标及适用范围。'
    const wrapper = mount(ReportBody, { props: { periodLabel: '单日试评', report: { ...report,
      items: [{ ...report.items![0], reason: `${basis}邻档判断：还需把验收条件说清楚。`,
        evidence: [{ ...record, supports: basis }] }]
    } } })
    expect(wrapper.text()).toContain('单日试评')
    expect(wrapper.text()).not.toContain('周评')
    const content = wrapper.get('.item-content').text()
    expect(content.split(basis)).toHaveLength(2)
    expect(content).toContain('为什么得到这个分数')
    expect(content).toContain('距离下一档还差什么')
    expect(content).toContain('还需把验收条件说清楚。')
    wrapper.unmount()
  })

  it('静态报告按精确值保留材质，不把舍入90.0误作90分档', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report, score: '90.0', exact: '2249/25' } } })
    expect(wrapper.find('.score-reveal').attributes('data-tone')).toBe('jade')
    expect(wrapper.find('.score-reveal__stage').exists()).toBe(false)
    expect(wrapper.find('.score-reveal').text()).toBe('90.0')
    wrapper.unmount()
  })

  it('临时结果恢复待评审，不展示参考分、分项或来源正文', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report, sample_initial: {
      version: 'test', sample_digest: 'test', audit_status: 'not_reviewed', method: 'observed_dimension_weights',
      score: '70.0', exact: '1749/25', covered_weight: 65, missing_dimensions: ['M']
    } } } })
    expect(wrapper.text()).toContain('评审待完成')
    expect(wrapper.find('.score-reveal').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('抽样')
    expect(wrapper.text()).not.toContain('70.0')
    expect(wrapper.text()).not.toContain(record.quote)
    expect(wrapper.find('[role=tablist]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('没有可展示总分或参考分时保留缺失状态，不生成分数特效', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report, score: null, exact: null, score_status: 'observation_only' } } })
    expect(wrapper.find('.score-reveal').exists()).toBe(false)
    wrapper.unmount()
  })

  it('关联分项明确说明不单独计分', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report,
      items: [{ ...report.items![0], state: 'related', score: null, exact: null, evidence: [] }]
    } } })
    expect(wrapper.text()).toContain('关联其他分项')
    expect(wrapper.text()).toContain('本项仅作关联说明，不单独计分。')
    wrapper.unmount()
  })

  it('用户只见可展示引用，不暴露受限案例及其综合归纳，分数仍为真实原值', () => {
    const wrapper = mount(ReportBody, { props: { report } })
    expect(wrapper.text()).toContain('智用力评分')
    expect(wrapper.text()).toContain('周评')
    expect(wrapper.text()).toContain('80.0')
    expect(wrapper.text()).toContain(record.quote)
    expect(wrapper.text()).toContain('整项原始评审理由。')
    expect(wrapper.text()).not.toContain('可联系管理员核对')
    for (const hidden of ['待核汇报稿', '受限的引用。', '来源事件存在截断', record.supports, '内部采集审查记录。']) {
      expect(wrapper.text()).not.toContain(hidden)
    }
    wrapper.unmount()
  })

  it('管理员保留完整依据、原判断和限制，使用同一个月评标题结构', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report, period_type: 'month' }, reviewDetails: true } })
    for (const visible of ['智用力评分', '月评', record.quote, '受限的引用。', '来源事件存在截断', record.supports, '内部采集审查记录。']) {
      expect(wrapper.text()).toContain(visible)
    }
    expect(wrapper.text()).not.toContain('本项判定说明未在个人报告中展示')
    wrapper.unmount()
  })
  it('保留维度Tab切换，切换后展示对应分项的真实理由', async () => {
    const dimensions = [{ code: 'P', name: '表达清晰', score: '80.0' }, { code: 'M', name: '任务掌控', score: '70.0' }] as Report['dimensions']
    const wrapper = mount(ReportBody, { props: { report: { ...report, dimensions,
      items: [report.items![0], { ...report.items![0], item_code: 'M1', name: '任务判断', reason: '对应M项的真实理由。', evidence: [] }] } } })
    expect(wrapper.get('[role=tabpanel]').text()).toContain('整项原始评审理由。')
    await wrapper.findAll('[role=tab]')[1].trigger('click')
    expect(wrapper.get('[role=tabpanel]').text()).toContain('对应M项的真实理由。')
    expect(wrapper.get('[role=tabpanel]').text()).not.toContain('整项原始评审理由。')
    wrapper.unmount()
  })

  it('新解读替代旧案例，两种角色共用正文且分数保持原值', () => {
    const value: Report = { ...report, task_review: { version: 'task-evidence-20261106.1', status: 'generated', task_keys: ['task-a'], checked_task_keys: [], scope_note: '仅本期材料', week_judgments: [], cases: [{ id: 'case-a', task_key: 'task-a', title: '页面修订任务', goal: '核对页面', outcome: '已保存修订内容', context_status: 'available_records', record_count: 1, claims: [{ text: '本人明确调整范围。', item_codes: ['P1'], record_ids: ['a'], limitations: [] }], limitations: [] }] } }
    const personal = mount(ReportBody, { props: { report: value } })
    const admin = mount(ReportBody, { props: { report: value, reviewDetails: true } })
    expect(personal.findComponent({ name: 'TaskReviewSection' }).text()).toBe(admin.findComponent({ name: 'TaskReviewSection' }).text())
    expect(personal.findComponent({ name: 'EvidenceCases' }).exists()).toBe(false)
    expect(personal.find('.score-reveal').text()).toBe('80.0')
    expect(personal.text()).not.toMatch(/已核查|待核查|待证明|待复核/)
    personal.unmount(); admin.unmount()
  })

})
