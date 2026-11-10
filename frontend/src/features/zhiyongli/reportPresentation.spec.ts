import { describe, expect, it } from 'vitest'
import type { Evidence, Item } from './api'
import { canDisplayAnalysis, evidenceForDisplay, explanation, explanationsForDisplay, observationsForDisplay, reportText, sourceContext, taskEvidence } from './reportPresentation'

function evidence(id: string, title: string, at = '2026-09-07T08:00:00Z'): Evidence {
  return { id, task_title: title, occurred_at: at, source_ref: 'source:event', source_hash: 'hash',
    role: 'user', origin: 'direct_expression', quote: '原话769，P1不做替换。', supports: '目标有明确交付对象。',
    context: '原任务背景（来源索引）：背景\n本项已核判断及限制：评审底稿\n来源原文片段：\n原话769，P1不做替换。', limitations: [] }
}
function item(records: Evidence[], reason = ''): Item {
  return { item_code: 'P1', name: '目标清楚', state: 'observed', exact: '90', score: '90.0', reason,
    criterion: '', necessary_gap: false, components: [], period_states: {}, evidence: records, limitations: [] }
}

describe('评分报告的用户可读说明', () => {
  it('用包内可核任务名称替代评审索引，同时保留评分与限制', () => {
    const display = reportText({ items: [item([evidence('lican:week:n769_g', '业务大厅页面修订')])] })
    expect(display('769要求写本次变化，后续769仍有文档不清。P1不代表J3已达标。'))
      .toBe('「业务大厅页面修订」要求写本次变化，后续「业务大厅页面修订」仍有文档不清。目标清楚不代表结果处理已达标。')
  })

  it('不会把日期、数量、分数、版本和技术参数替换成同号任务', () => {
    const display = reportText({ items: [item(['2', '80', '769', '2026'].map(n => evidence(`u:w:n${n}_a`, `任务${n}`)))] })
    const text = '2026-09-07，2个任务，2套方案，2项限制，80分，80档，769行，v769.2，769MB，769px，2FA，2026/9/7。'
    expect(display(text)).toBe(text)
    expect(display('2指出两套启动方式。')).toBe('「任务2」指出两套启动方式。')
  })

  it('索引对应不唯一时保留原文，不猜测归属', () => {
    const display = reportText({ items: [item([evidence('u:w:n769_a', '任务一'), evidence('u:w:n769_b', '任务二')])] })
    expect(display('769有遗漏。')).toBe('769有遗漏。')
  })

  it('按周分开月度说明，保留限制并拆分得分依据', () => {
    const result = explanation(item([], '2026-09-07：目标明确。成果仍未验收。 邻档判断：90有对应依据；未达到100。\n2026-09-14：资料不足，不补分。'), s => s)
    expect(result).toEqual([
      { date: '2026-09-07', analysis: ['目标明确。', '成果仍未验收。'], basis: ['90有对应依据；', '未达到100。'] },
      { date: '2026-09-14', analysis: ['资料不足，不补分。'], basis: [] }
    ])
  })

  it('保留全部任务与引用，按任务内时间排列，不更改原文', () => {
    const late = evidence('u:w:n769_b', '页面修订', '2026-09-08T08:00:00Z')
    const early = evidence('u:w:n769_a', '页面修订')
    const sheet = evidence('u:w:n627_a', '功能矩阵')
    const original = item([late, sheet, early])
    expect(taskEvidence(original).map(t => [t.title, t.records.map(r => r.id)]))
      .toEqual([['页面修订', [early.id, late.id]], ['功能矩阵', [sheet.id]]])
    expect(original.evidence).toEqual([late, sheet, early])
    expect(sourceContext(early)).toBe(`任务背景：背景\n\n对话原文：\n${early.quote}`)
    expect(sourceContext({ ...early, context: '没有包装标签的原始上下文' })).toBe('没有包装标签的原始上下文')
    const unknownContext = '其他背景\n来源原文片段：\n原话'
    expect(sourceContext({ ...early, context: unknownContext })).toBe(unknownContext)
  })

  it('受限引用不公开，也不将整项归纳照搬给剩余案例；原评分与底稿保持不变', () => {
    const valid = evidence('u:w:n769_a', '页面修订')
    const limited = { ...evidence('u:w:n1753_a', '汇报稿'), limitations: ['来源事件存在截断，仅使用已保存的可核部分。'] }
    const original = item([valid, limited], '涉及页面修订和汇报稿的综合判断。')
    expect(evidenceForDisplay(original)).toEqual([valid])
    expect(canDisplayAnalysis(original)).toBe(false)
    expect(observationsForDisplay(original, s => s)).toEqual([])
    expect(explanationsForDisplay(original, s => s)).toEqual([])
    expect(original.evidence).toEqual([valid, limited])
    expect(original.score).toBe('90.0')
    expect(original.reason).toBe('涉及页面修订和汇报稿的综合判断。')
    expect(canDisplayAnalysis(item([valid]))).toBe(true)
  })

  it('归属不清、AI动作和无法在上下文定位的引用不作为本人案例', () => {
    const valid = evidence('u:w:n769_a', '页面修订')
    const records = [valid,
      { ...valid, id: 'unclear', origin: 'uncertain' },
      { ...valid, id: 'forwarded', origin: 'quoted_or_forwarded' },
      { ...valid, id: 'ai', role: 'assistant' as const },
      { ...valid, id: 'missing', context: '无此引文' }
    ]
    expect(evidenceForDisplay(item(records))).toEqual([valid])
    expect(evidenceForDisplay({ ...item([valid]), state: 'pending' })).toEqual([])
    expect(evidenceForDisplay({ ...item([valid]), limitations: ['该判断仍待核对'] })).toEqual([])
    expect(canDisplayAnalysis(item([]))).toBe(false)
  })

  it('保留有完整依据的独立结论和月份内对应周的说明，不混用别周材料', () => {
    const valid = { ...evidence('u:w:n769_a', '页面修订'), supports: '页面修订范围明确。' }
    const limited = { ...evidence('u:w:n1753_a', '汇报稿', '2026-09-14T08:00:00Z'), supports: '汇报稿目标明确。', limitations: ['来源受限'] }
    const month = { ...item([valid, limited], '2026-09-07：页面修订范围明确。\n2026-09-14：汇报稿的综合判断。'),
      components: [
        { period_start: '2026-09-07', period_end: '2026-09-14', exact: '90', necessary_gap: false },
        { period_start: '2026-09-14', period_end: '2026-09-21', exact: '80', necessary_gap: false }
      ] }
    expect(observationsForDisplay(month, s => s)).toEqual(['页面修订范围明确。'])
    expect(explanationsForDisplay(month, s => s)).toEqual([
      { date: '2026-09-07', analysis: ['页面修订范围明确。'], basis: [] }
    ])
  })
})
