import { useMemo } from 'react'
import { Marked, Renderer } from 'marked'
import { sourceLink } from '@/utils/source-link'

const escape = (text: string) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export default function Report({ content }: { content: string }) {
  const html = useMemo(() => {
    const renderer = new Renderer()
    renderer.html = token => escape(token.text)
    renderer.image = token => escape(token.text || '')
    renderer.link = function(token) {
      const link = sourceLink(token.href)
      const label = this.parser.parseInline(token.tokens)
      return link ? `<a href="${escape(link)}" target="_blank" rel="noopener noreferrer">${label}</a>` : label
    }
    return new Marked({ renderer, gfm: true }).parse(content) as string
  }, [content])
  return <div className="research-report" dangerouslySetInnerHTML={{ __html: html }} />
}
