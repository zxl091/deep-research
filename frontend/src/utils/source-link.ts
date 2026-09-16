// 将知识库引用定位到当前站点的文档切片页，保留后端的稳定来源标识。
export function sourceLink(value?: string): string | undefined {
  if (!value) return undefined
  const local = /^local:\/\/kb\/([0-9a-f-]{36})\/([0-9a-f-]{36})$/i.exec(value)
  if (local) return `/knowledge?kb_id=${local[1]}&doc_id=${local[2]}`
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}

export function openSource(value?: string) {
  const link = sourceLink(value)
  if (!link) return
  if (link.startsWith('/')) window.location.assign(link)
  else window.open(link, '_blank', 'noopener,noreferrer')
}

export function researchReference(ref: Record<string, any>, index: number) {
  const link = ref.link || ref.url || ref.source_url || ''
  return {
    id: index + 1,
    title: ref.title || ref.source_name || (ref.source !== 'knowledge' && ref.source !== 'web' ? ref.source : '') || '来源',
    link,
    content: ref.content || ref.summary || '',
    source: (link.startsWith('local://') || ref.source === 'knowledge' || ref.source_type === 'local' ? 'knowledge' : 'web') as 'knowledge' | 'web',
  }
}
