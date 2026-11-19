// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import type { TaskRecord } from './api'
import { groupRecordText, renderReportText } from './reportText'

describe('评审正文与来源展示', () => {
  it('保留Markdown段落和列表，来源HTML、脚本与图片均不执行加载', () => {
    const body = document.createElement('div')
    body.innerHTML = renderReportText('## 核验安排\n\n- 比较来源\n- 说明限制\n\n<img src="https://example.test/leak" onerror="alert(1)">\n<script>alert(1)</script>\n\n![来源图片](https://example.test/image)\n[危险链接](javascript:alert(1))')
    expect(body.querySelector('h2')?.textContent).toBe('核验安排')
    expect(body.querySelectorAll('li')).toHaveLength(2)
    expect(body.querySelector('img,script')).toBeNull()
    expect(body.querySelector('[onerror],[href^="javascript:"]')).toBeNull()
    expect(body.textContent).toContain('来源图片')
  })

  it('完全同文只显示一份正文，发生记录和限制完整保留，角色或归属不同不合并', () => {
    const base: TaskRecord = { id: 'one', source_hash: 'source-one', text_hash: 'text',
      occurred_at: '2026-11-16T10:00:00+08:00', role: 'user', origin: 'direct_expression',
      text: '请补充失败场景验证。', limitations: ['仅本次输入'] }
    const records: TaskRecord[] = [base, { ...base, id: 'two', source_hash: 'source-two',
      occurred_at: '2026-11-17T10:00:00+08:00', limitations: ['后续再提出'] },
      { ...base, id: 'three', role: 'assistant', origin: 'assistant_output' },
      { ...base, id: 'four', origin: 'unattributed' }]
    const original = JSON.stringify(records)
    const groups = groupRecordText(records)
    expect(groups).toHaveLength(3)
    expect(groups[0]!.occurrences.map(record => record.id)).toEqual(['one', 'two'])
    expect(groups[0]!.limitations).toEqual(['仅本次输入', '后续再提出'])
    expect(JSON.stringify(records)).toBe(original)
  })
})
