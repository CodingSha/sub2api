import type { Evidence, Item, Report } from './api'

const itemNames: Record<string, string> = {
  P1: '目标清楚', P2: '信息充分', P3: '要求约束',
  M1: '方法分工', M2: '依赖推进', M3: '调整执行',
  J1: '判断依据', J2: '核验安排', J3: '结果处理',
  E1: '交互组织', E2: '失效止损', E3: '复用自动化'
}
const dimensionNames: Record<string, string> = {
  P: '提示词质量', M: '任务驾驭度', J: '判断核验', E: '使用效率'
}

export const scoreStatusLabel = (report: Pick<Report, 'score_status'>) => ({
  scored: '暂无综合分', observation_only: '暂未形成总分', insufficient_evidence: '资料不足',
  no_activity: '暂无可评记录', data_pending: '评分待完成'
})[report.score_status]

export const scoreNeedsAttention = (report: Pick<Report, 'score_status' | 'score' | 'period_type' | 'rating'>) =>
  report.score_status === 'observation_only' || report.score_status === 'insufficient_evidence' ||
  (report.period_type === 'month' && report.score !== null && report.rating === null)

// 旧结果包的 n<任务索引>_<引用名> 来自 evidence_for；只采用包内唯一对应，绝不猜任务。
export function reportText(report: Pick<Report, 'items'>) {
  const titles = new Map<string, Set<string>>()
  for (const item of report.items ?? []) {
    for (const evidence of item.evidence ?? []) {
      const reference = evidence.id.match(/:n(\d+)_/)
      if (!reference || !evidence.task_title) continue
      const names = titles.get(reference[1]) ?? new Set<string>()
      names.add(evidence.task_title)
      titles.set(reference[1], names)
    }
  }
  return (text: string) => text
    .replace(/(?<![\w./-])(\d+)(?![\w./%-])/g, (value, id: string, offset: number, source: string) => {
      const names = titles.get(id)
      // 数量、日期、分数和技术参数保持原样，不能把同值数字误认作任务索引。
      const tail = source.slice(offset + id.length)
      if (names?.size !== 1 || /^(?:分|档|个|条|处|次|天|日|周|月|年|秒|分钟|小时|行|套|项|人|字|页|种|步|轮|%)/.test(tail)) return value
      return `「${[...names][0]}」`
    })
    .replace(/\b([PMJE][1-3])\b/g, (code) => itemNames[code])
    .replace(/\b([PMJE])维度/g, (_, code: string) => dimensionNames[code])
    .replace(/\b(?:023|024)：/g, '')
    .replace(/\be\d{4}\b/g, '相关对话记录')
}

export function paragraphs(text: string) {
  return text.match(/[^。！？\n]+[。！？]?/g)
    ?.flatMap(s => s.split(/(?<=；)|(?<=，)(?=「)/)).map(s => s.trim()).filter(Boolean) ?? []
}

export function explanation(item: Item, display: (text: string) => string) {
  return item.reason.split('\n').filter(Boolean).map(line => {
    const date = line.match(/^(\d{4}-\d{2}-\d{2})[：:]/)
    const content = date ? line.slice(date[0].length) : line
    const [analysis, ...basis] = content.split('邻档判断：')
    return {
      date: date?.[1],
      analysis: paragraphs(display(analysis)),
      basis: paragraphs(display(basis.join('邻档判断：')))
    }
  })
}

// 已核评分依据不自动等于可公开的案例；存在材料限制时，不替评审者推定限制无影响。
export function evidenceForDisplay(item: Item) {
  if (item.state !== 'observed' || item.limitations?.length) return []
  return (item.evidence ?? []).filter(e =>
    e.role === 'user' && ['direct_expression', 'explicit_adoption'].includes(e.origin) &&
    !e.limitations?.length && e.quote.trim() && e.supports.trim() && e.context.includes(e.quote)
  )
}

export function canDisplayAnalysis(item: Item, records = item.evidence ?? []) {
  const approved = new Set(evidenceForDisplay(item))
  // supports 是整项综合判断；删去任一来源后，不能照搬原判断为剩余引文背书。
  return records.length > 0 && records.every(record => approved.has(record))
}

export function observationsForDisplay(item: Item, display: (text: string) => string) {
  const claims = new Map<string, Evidence[]>()
  for (const record of item.evidence ?? []) {
    const records = claims.get(record.supports) ?? []
    records.push(record)
    claims.set(record.supports, records)
  }
  return [...claims].filter(([, records]) => canDisplayAnalysis(item, records)).map(([claim]) => display(claim))
}

export function explanationsForDisplay(item: Item, display: (text: string) => string) {
  return explanation(item, display).filter(section => {
    if (!section.date) return canDisplayAnalysis(item)
    // 月报的每段理由对应一个原生期间，不能因别周材料受限就撤掉已核完整的这一周。
    const period = item.components?.find(component => component.period_start === section.date)
    if (!period) return false
    const start = Date.parse(`${period.period_start}T00:00:00+08:00`)
    const end = Date.parse(`${period.period_end}T00:00:00+08:00`)
    const records = (item.evidence ?? []).filter(record => {
      const at = Date.parse(record.occurred_at)
      return at >= start && at < end
    })
    return canDisplayAnalysis(item, records)
  })
}

export function taskEvidence(item: Item) {
  const tasks = new Map<string, Evidence[]>()
  for (const evidence of item.evidence ?? []) {
    const records = tasks.get(evidence.task_title) ?? []
    records.push(evidence)
    tasks.set(evidence.task_title, records)
  }
  return [...tasks].map(([title, records]) => ({
    title,
    records: records.sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
  }))
}

// 这些是导出时添加的包装标签；引文与来源片段原样保留，不对用户原话做编号替换。
export function sourceContext(evidence: Evidence) {
  const marker = '\n来源原文片段：\n'
  const offset = evidence.context.indexOf(marker)
  if (offset < 0) return evidence.context
  const background = evidence.context.match(/^原任务背景（来源索引）：([\s\S]*?)\n本项已核判断及限制：/)
  const excerpt = evidence.context.slice(offset + marker.length)
  return background ? `任务背景：${background[1]}\n\n对话原文：\n${excerpt}` : evidence.context
}

export { dimensionNames }
