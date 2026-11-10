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
  it('用户只见可展示引用，不暴露受限案例及其综合归纳，分数仍为真实原值', () => {
    const wrapper = mount(ReportBody, { props: { report } })
    expect(wrapper.text()).toContain('智用力评分')
    expect(wrapper.text()).toContain('周评')
    expect(wrapper.text()).toContain('80.0')
    expect(wrapper.text()).toContain(record.quote)
    for (const hidden of ['待核汇报稿', '受限的引用。', '来源事件存在截断', record.supports, '整项原始评审理由。', '内部采集审查记录。']) {
      expect(wrapper.text()).not.toContain(hidden)
    }
    wrapper.unmount()
  })

  it('管理员保留完整依据、原判断和限制，使用同一个月评标题结构', () => {
    const wrapper = mount(ReportBody, { props: { report: { ...report, period_type: 'month' }, reviewDetails: true } })
    for (const visible of ['智用力评分', '月评', record.quote, '受限的引用。', '来源事件存在截断', record.supports, '整项原始评审理由。', '内部采集审查记录。']) {
      expect(wrapper.text()).toContain(visible)
    }
    wrapper.unmount()
  })
})
