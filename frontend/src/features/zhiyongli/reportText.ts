import { marked, Renderer } from 'marked'
import DOMPurify from 'dompurify'
import type { TaskRecord } from './api'

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const renderer = new Renderer()
// 来源和模型文字均按正文处理，HTML及图片保留为文字，不执行或加载。
renderer.html = token => escape(token.text)
renderer.image = token => escape(token.raw)
export const renderReportText = (text: string) => DOMPurify.sanitize(
  marked.parse(text, { async: false, breaks: true, renderer }) as string, {
    ALLOWED_TAGS: ['p','br','ul','ol','li','strong','em','code','pre','blockquote','h1','h2','h3','h4','table','thead','tbody','tr','th','td','hr','del','a'],
    ALLOWED_ATTR: ['href','title']
  }
)

export function groupRecordText(records: TaskRecord[]) {
  const groups: { record: TaskRecord; occurrences: TaskRecord[]; limitations: string[] }[] = []
  const index = new Map<string, (typeof groups)[number]>()
  for (const record of records) {
    // 只合并角色、归属和正文完全相同的展示，保留每个发生记录及其限制。
    const key = JSON.stringify([record.role, record.origin, record.text])
    const group = index.get(key)
    if (group) {
      group.occurrences.push(record)
      group.limitations = [...new Set([...group.limitations, ...(record.limitations ?? [])])]
    } else {
      const entry = { record, occurrences: [record], limitations: [...(record.limitations ?? [])] }
      index.set(key, entry)
      groups.push(entry)
    }
  }
  return groups
}
