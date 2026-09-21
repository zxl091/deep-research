import { useMemo } from 'react'
import { Marked, Renderer } from 'marked'
import { sourceLink } from '@/utils/source-link'

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export default function Report({ content, headings = false, onSource, evidenceIds = [], onCitation }: { content: string; headings?: boolean; onSource?: (url: string) => boolean; evidenceIds?: string[]; onCitation?: (id: string) => void }) {
  const html = useMemo(() => {
    const renderer = new Renderer()
    let index = 0
    if (headings) renderer.heading = function(token) {
      return `<h${token.depth} id="report-heading-${index++}">${this.parser.parseInline(token.tokens)}</h${token.depth}>`
    }
    renderer.html = token => escape(token.text)
    renderer.image = token => escape(token.text || '')
    if (evidenceIds.length) renderer.text = function(token) {
      if ('tokens' in token && token.tokens) return this.parser.parseInline(token.tokens)
      return token.text.replace(/\[([A-Za-z]*\d+)\]/g, (match, id: string) => evidenceIds.includes(id)
        ? `<button type="button" class="report-citation" data-citation="${escape(id)}" aria-label="查看来源 ${escape(id)}">${escape(match)}</button>` : match)
    }
    renderer.link = function(token) {
      const link = sourceLink(token.href)
      const label = this.parser.parseInline(token.tokens)
      return link ? `<a href="${escape(link)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label
    }
    return new Marked({ renderer, gfm: true }).parse(content) as string
  }, [content, headings, evidenceIds])
  return <div className="research-report" onClick={event => {
    const citation = (event.target as HTMLElement).closest<HTMLElement>('[data-citation]')
    if (citation?.dataset.citation) { onCitation?.(citation.dataset.citation); return }
    const anchor = (event.target as HTMLElement).closest('a')
    if (anchor && onSource?.(anchor.getAttribute('href') || '')) event.preventDefault()
  }} dangerouslySetInnerHTML={{ __html: html }} />
}
