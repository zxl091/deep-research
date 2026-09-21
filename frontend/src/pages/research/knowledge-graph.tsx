import { useEffect, useMemo, useRef, useState } from 'react'
import type { ECharts, EChartsOption } from 'echarts'
import type { ResearchGraph } from '@/api/assistant'
import './knowledge-graph.scss'

const categories: Record<string, { name: string; color: string }> = {
  core: { name: '核心概念', color: '#377f70' },
  tech: { name: '技术', color: '#6287ad' },
  company: { name: '企业 / 机构', color: '#bc9459' },
  policy: { name: '政策', color: '#9474a6' },
  product: { name: '产品', color: '#c07878' },
  person: { name: '人物', color: '#678f85' },
  other: { name: '其他', color: '#8b9195' },
}

function GraphView({ graph }: { graph: ResearchGraph }) {
  const container = useRef<HTMLDivElement>(null)
  const chart = useRef<ECharts | undefined>(undefined)
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const data = useMemo(() => {
    const unique = new Map<string, ResearchGraph['nodes'][number]>()
    for (const node of graph.nodes || []) {
      if (node && typeof node.id === 'string' && node.id && typeof node.name === 'string' && node.name) unique.set(node.id, node)
    }
    const nodes = [...unique.values()]
    const edges = (graph.edges || []).filter(edge => edge && unique.has(edge.source) && unique.has(edge.target))
    return { nodes, edges, byId: unique }
  }, [graph])

  useEffect(() => {
    if (!container.current) return
    let disposed = false
    let instance: ECharts | undefined
    setError('')
    const observer = new ResizeObserver(() => instance?.resize())
    observer.observe(container.current)
    void import('echarts').then(ec => {
      if (disposed || !container.current) return
      instance = ec.init(container.current)
      chart.current = instance
      const kinds = [...new Set(data.nodes.map(n => categories[n.type || ''] ? n.type! : 'other'))]
      const option: EChartsOption = {
        animationDurationUpdate: 300,
        tooltip: { renderMode: 'richText', confine: true },
        series: [{
          type: 'graph', layout: 'force', roam: true, draggable: true,
          top: 55, bottom: 35, left: 25, right: 25,
          zoom: .85, scaleLimit: { min: .25, max: 3 },
          force: { repulsion: 450, edgeLength: [100, 180], gravity: .12 },
          categories: kinds.map(k => ({ name: categories[k].name, itemStyle: { color: categories[k].color } })),
          data: data.nodes.map(node => {
            const kind = categories[node.type || ''] ? node.type! : 'other'
            const size = Number(node.size || 28 + (node.importance || 5) * 2)
            return { id: node.id, name: node.name, category: kinds.indexOf(kind),
              symbolSize: Number.isFinite(size) ? Math.min(60, Math.max(28, size)) : 38,
              label: { show: true, position: 'bottom', color: '#414740', fontSize: 11,
                formatter: () => node.name.length > 10 ? node.name.slice(0, 10) + '\n' + node.name.slice(10) : node.name },
              tooltip: { formatter: () => node.name + '\n' + categories[kind].name },
            }
          }),
          links: data.edges.map(edge => ({ source: edge.source, target: edge.target,
            label: { show: true, formatter: () => edge.relation || '相关', fontSize: 10, color: '#777d76' },
            tooltip: { formatter: () => `${data.byId.get(edge.source)?.name} → ${data.byId.get(edge.target)?.name}\n${edge.relation || '相关'}` },
          })),
          edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 7,
          lineStyle: { color: '#b4beb7', width: 1.4, curveness: .08 },
          emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
        }],
      }
      instance.setOption(option)
      instance.on('click', params => {
        const point = params.data as { id?: string } | undefined
        if (params.dataType === 'node' && point?.id) setSelected(point.id)
      })
    }).catch(() => { if (!disposed) setError('图谱加载失败，可先查看下方关系明细。') })
    return () => { disposed = true; observer.disconnect(); instance?.dispose(); chart.current = undefined }
  }, [data])

  function zoom(factor: number) {
    const instance = chart.current
    if (!instance) return
    const current = (instance.getOption().series as { zoom?: number }[])[0]?.zoom || .85
    instance.setOption({ series: [{ zoom: Math.max(.25, Math.min(3, current * factor)) }] })
  }
  const relations = selected ? data.edges.filter(e => e.source === selected || e.target === selected) : data.edges
  return <div className="research-knowledge-graph">
    <div className="graph-toolbar">
      <span>拖动节点 · 滚轮缩放 · 点击查看关系</span>
      <div>
        <button type="button" aria-label="缩小图谱" onClick={() => zoom(.8)}>−</button>
        <button type="button" aria-label="放大图谱" onClick={() => zoom(1.25)}>＋</button>
        <button type="button" onClick={() => { chart.current?.setOption({ series: [{ zoom: .85, center: ['50%', '50%'] }] }); setSelected('') }}>重置视图</button>
      </div>
    </div>
    {error && <p role="alert">{error}</p>}
    <div className="graph-legend" aria-label="实体分类">
      {[...new Set(data.nodes.map(node => categories[node.type || ''] ? node.type! : 'other'))].map(kind =>
        <span key={kind}><i style={{ background: categories[kind].color }} />{categories[kind].name}</span>)}
    </div>
    <div ref={container} className="graph-canvas" role="img" aria-label={`研究知识图谱，${data.nodes.length} 个实体，${data.edges.length} 条关系；下方提供文字明细`} />
    <div className="graph-relations">
      <label>查看实体关系 <select aria-label="选择图谱实体" value={data.byId.has(selected) ? selected : ''} onChange={event => setSelected(event.target.value)}>
        <option value="">全部实体</option>
        {data.nodes.map(node => <option key={node.id} value={node.id}>{node.name}</option>)}
      </select></label>
      {relations.length ? <ul>{relations.map((edge, index) => <li key={`${edge.source}-${edge.target}-${index}`}>
        <strong>{data.byId.get(edge.source)?.name}</strong><span>{edge.relation || '相关'} →</span><strong>{data.byId.get(edge.target)?.name}</strong>
      </li>)}</ul> : <p>当前实体尚未生成关联关系。</p>}
    </div>
    <p className="graph-note">关系由模型根据本次资料整理，辅助理解研究主题；具体结论请结合报告及来源核对。</p>
  </div>
}

export default function KnowledgeGraph({ graph, expanded = false }: { graph?: ResearchGraph; expanded?: boolean }) {
  const [open, setOpen] = useState(false)
  if (!graph?.nodes?.length) return null
  if (expanded) return <GraphView graph={graph} />
  return <details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>知识图谱 · {graph.nodes.length} 个实体 · {graph.edges?.length || 0} 条关系</summary>
    {open && <GraphView graph={graph} />}
  </details>
}
