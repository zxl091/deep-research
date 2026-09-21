import { Marked } from 'marked'
import type { ResearchRun } from '@/api/assistant'

export const runActive = (run: ResearchRun | null) => !!run && ['queued', 'running'].includes(run.status)
export const statusLabels: Record<string, string> = { queued: '等待开始', running: '正在研究', completed: '研究完成', partial: '部分完成', cancelled: '已停止', interrupted: '运行中断', failed: '执行失败' }
export const sourceLabels: Record<string, string> = { knowledge: '个人知识库', database: '业务查询快照', web: '公开网页' }

export function reportStructure(content: string) {
  const marked = new Marked()
  const tokens = marked.lexer(content)
  let heading = 0
  let section = '研究数据'
  const headings: { id: string; title: string; depth: number }[] = []
  const tables: { title: string; content: string }[] = []
  marked.walkTokens(tokens, token => {
    if (token.type === 'heading') {
      section = token.text.replace(/[*_`]/g, '')
      headings.push({ id: `report-heading-${heading++}`, title: section, depth: token.depth })
    }
    if (token.type === 'table') tables.push({ title: section, content: token.raw })
  })
  return { headings, tables }
}

export function downloadText(content: string, filename: string, mime = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob(['\uFEFF', content], { type: mime }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = [...filename].filter(char => char.charCodeAt(0) >= 32).join('').replace(/[<>:"/\\|?*]/g, '_').slice(0, 120)
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
