import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Select, Spin } from 'antd'
import { FileTextOutlined, ArrowRightOutlined, ReadOutlined, LinkOutlined, TableOutlined } from '@ant-design/icons'
import type { Evidence, ResearchRun } from '@/api/assistant'
import Report from './report'
import FullReportDetails from './full-report-details'
import KnowledgeGraph from './knowledge-graph'
import { reportStructure, runActive, sourceLabels, statusLabels } from './result-utils'

const tabs = [{ id: 'report', label: '研究报告' }, { id: 'tables', label: '数据表' }, { id: 'charts', label: '图表' }, { id: 'graph', label: '知识图谱' }, { id: 'sources', label: '来源' }] as const
type ResultTab = typeof tabs[number]['id']

function EmptyResult({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="result-empty"><ReadOutlined /><h2>{title}</h2><p>{children}</p></div>
}

export default function ResultWorkbench({ run, runs, onSelect, onEvidence }: {
  run: ResearchRun | null; runs: ResearchRun[]; onSelect: (id: string) => void; onEvidence: (evidence: Evidence) => void
}) {
  const [tab, setTab] = useState<ResultTab>('report')
  const [currentHeading, setCurrentHeading] = useState('')
  const scroller = useRef<HTMLDivElement>(null)
  const structure = useMemo(() => reportStructure(run?.report || ''), [run?.report])
  useEffect(() => { setTab('report'); setCurrentHeading(''); scroller.current?.scrollTo(0, 0) }, [run?.id])
  useEffect(() => {
    if (tab !== 'report' || !scroller.current) return
    const observer = new IntersectionObserver(entries => {
      const entry = entries.find(item => item.isIntersecting)
      if (entry) setCurrentHeading(entry.target.id)
    }, { root: scroller.current, rootMargin: '0px 0px -65% 0px' })
    scroller.current.querySelectorAll('.report-document [id^="report-heading-"]').forEach(el => observer.observe(el))
    return () => observer.disconnect()
  }, [tab, run?.report])
  const openTab = (next: ResultTab) => { setTab(next); scroller.current?.scrollTo(0, 0) }
  const openSource = (url: string) => {
    const match = run?.state.evidence.find(item => item.url === url)
    if (!match) return false
    onEvidence(match)
    return true
  }
  const charts = run?.state.charts || []
  const evidence = run?.state.evidence || []
  const title = structure.headings[0]?.depth === 1 ? structure.headings[0].title : run?.query
  const toc = structure.headings.filter(item => item.depth <= 3)
  const sortedRuns = [...runs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))

  return <section className="result-workbench" aria-label="研究成果工作区">
    <div className="result-toolbar">
      <div className="result-tabs" role="tablist" aria-label="成果类型">{tabs.map(item => <button key={item.id} id={`result-tab-${item.id}`} role="tab" aria-selected={tab === item.id} aria-controls="result-content" tabIndex={tab === item.id ? 0 : -1} onKeyDown={event => {
        const index = tabs.findIndex(t => t.id === item.id)
        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1
        if (next >= 0) { event.preventDefault(); openTab(tabs[next].id); document.getElementById(`result-tab-${tabs[next].id}`)?.focus() }
      }} onClick={() => openTab(item.id)}>{item.label}{item.id === 'sources' && !!evidence.length && <small>{evidence.length}</small>}</button>)}</div>
      {!!runs.length && <Select className="result-version" aria-label="选择研究成果" value={run?.id} onChange={onSelect} popupMatchSelectWidth={300} options={sortedRuns.map((r, index) => ({ value: r.id, label: `第 ${index + 1} 次 · ${r.query}` }))} />}
    </div>
    <div className="result-scroll" ref={scroller} id="result-content" role="tabpanel" aria-labelledby={`result-tab-${tab}`} tabIndex={0}>
      {!run ? <div className="blank-document">
        <span className="document-eyebrow">YOUR RESEARCH WORKSPACE</span>
        <h1>从一个问题，<br />到一份有依据的研究。</h1>
        <p>在左侧提出研究主题。报告、数据与关系图谱，<br />将在这里逐步整理成形。</p>
        <div className="blank-document-rule" />
        <div className="blank-document-features"><span><FileTextOutlined />完整研究报告<small>结构清晰的分析与结论</small></span><span><TableOutlined />数据与图表<small>可查看原文和统计口径</small></span><span><LinkOutlined />关系与来源<small>联系研究对象，回看依据</small></span></div>
        <div className="blank-document-foot">DEEPRESEARCH <span>个人资料 × 公开信息 × 业务数据</span></div>
      </div> : <>
        {tab === 'report' && <div className="report-reading-layout">
          <nav className="report-outline" aria-label="报告目录"><span>目录</span>
            {toc.length ? toc.map(item => <button key={item.id} className={`${currentHeading === item.id ? 'is-current' : ''} depth-${item.depth}`} onClick={() => {
              const target = scroller.current?.querySelector<HTMLElement>(`#${item.id}`)
              target?.scrollIntoView({ behavior: 'smooth', block: 'start' }); setCurrentHeading(item.id)
            }}>{item.title}</button>) : (run.state.outline || []).map(item => <span className="outline-pending" key={item.id}>{item.title}</span>)}
            {!toc.length && !run.state.outline?.length && <p>研究展开后，章节会显示在这里。</p>}
          </nav>
          <article className="report-document">
            {run.state.chart_repair && <p className="data-note">图表已单独更新，共 {charts.length} 张；正文与审核沿用原版本。文末原有图表限制请结合最新“图表”页核对。</p>}
            <div className="document-meta"><span className="document-eyebrow">{run.state.engine === 'full_research_v2' ? 'RESEARCH REPORT' : 'RESEARCH NOTES'}</span><span>{new Date(run.created_at).toLocaleDateString('zh-CN')}</span></div>
            <h1 className="document-title">{title}</h1>
            <div className="document-subtitle"><span>{statusLabels[run.status] || run.status} · {evidence.length} 项来源</span><button onClick={() => openTab('sources')}>查看研究依据 <ArrowRightOutlined /></button></div>
            {run.report ? <div className={structure.headings[0]?.depth === 1 ? 'document-content has-title' : 'document-content'}><Report content={run.report} headings onSource={openSource} evidenceIds={evidence.map(item => item.id)} onCitation={id => { const item = evidence.find(e => e.id === id); if (item) onEvidence(item) }} /></div> : <div className="document-pending">{runActive(run) ? <Spin /> : <FileTextOutlined />}<h2>{runActive(run) ? '研究正在展开' : '本次研究尚未生成完整报告'}</h2><p>{run.state.error || (runActive(run) ? '已完成的章节会先显示在下方，最终报告将在这里汇总。' : '可以先查看已有章节、数据或来源。')}</p></div>}
            <FullReportDetails run={run} section="chapters" />
            {!!charts.length && <div className="document-artifacts"><div><FileTextOutlined /><span>本次研究另有 {charts.length} 张图表<small>查看可视化分析及对应原文数据</small></span></div><Button onClick={() => openTab('charts')}>查看图表 <ArrowRightOutlined /></Button></div>}
            {!!run.state.gaps?.length && <details className="document-gaps"><summary>研究范围与待补充资料 · {run.state.gaps.length}</summary><ul>{run.state.gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></details>}
            <FullReportDetails run={run} section="review" />
            <footer className="document-footer"><span>DEEPRESEARCH</span><span>基于本次检索资料整理 · 请结合引用核对结论</span></footer>
          </article>
        </div>}
        {tab === 'tables' && <div className="result-collection"><div className="collection-heading"><span className="document-eyebrow">RESEARCH DATA</span><h1>数据表</h1><p>汇集报告中的表格与图表数据，保留数值、统计期间及原文依据。</p></div>
          <FullReportDetails run={run} section="data" />
          {!structure.tables.length && !run.state.chart_evidence_records?.records?.length && !charts.some(chart => chart.data_contract?.points.length) && <EmptyResult title="暂无结构化数据表">本次结果尚未包含表格或可展示的图表数据。来源中的业务查询快照仍可查看。</EmptyResult>}
          {structure.tables.map((table, index) => <section className="data-sheet" key={index}><h2><span>{String(index + 1).padStart(2, '0')}</span>{table.title}</h2><Report content={table.content} onSource={openSource} /></section>)}
          {charts.filter(chart => chart.data_contract?.points.length).map((chart, index) => <section className="data-sheet" key={chart.id || index}><h2>{chart.title || `图表 ${index + 1}`}<small>图表原始数据</small></h2><div className="data-table-scroll"><table><thead><tr><th>对象 / 系列</th><th>指标</th><th>统计期间</th><th>数值</th><th>单位</th><th>数值性质</th><th>原文依据</th></tr></thead><tbody>{chart.data_contract!.points.map((point, n) => <tr key={n}><td>{point.label}{point.series && <small>{point.series}</small>}</td><td>{point.observation?.metric || point.metric || '未标注'}</td><td>{point.period}<small>{point.period_basis || point.period_note}</small></td><td>{({ at_least: '≥', at_most: '≤', more_than: '>', less_than: '<', approximate: '≈' } as Record<string, string>)[point.qualifier || '']}{point.value}</td><td>{point.unit}</td><td>{({ actual: '实际值', forecast: '预测值', target: '目标值' } as Record<string, string>)[point.value_kind] || point.value_kind}</td><td><button onClick={() => onEvidence({ id: `图表数据 ${n + 1}`, title: point.label, url: point.source_url, content: [point.quote, point.context_quote, point.period_note, point.conversion_note].filter(Boolean).join('\n\n'), source: 'web' })}>查看原文 ↗</button></td></tr>)}</tbody></table></div>{chart.data_contract?.coverage_note && <p className="data-note">{chart.data_contract.coverage_note}</p>}</section>)}
        </div>}
        {tab === 'charts' && <div className="result-collection"><div className="collection-heading"><span className="document-eyebrow">VISUAL ANALYSIS</span><h1>研究图表</h1><p>每张图表保留数据与原文，展开即可核对。</p></div>{!charts.length && <EmptyResult title="暂未生成可展示的图表">图表只展示已取得的数据；有待处理项时会列在下方。</EmptyResult>}<FullReportDetails run={run} section="charts" /></div>}
        {tab === 'graph' && <div className="result-collection graph-collection"><div className="collection-heading"><span className="document-eyebrow">KNOWLEDGE MAP</span><h1>知识图谱</h1><p>从整体关系理解研究主题，点击节点查看关联明细。</p></div>{run.state.knowledge_graph?.nodes?.length ? <KnowledgeGraph graph={run.state.knowledge_graph} expanded /> : <EmptyResult title="暂无关系图谱">研究整理出实体及关系后，会在这里展示。</EmptyResult>}</div>}
        {tab === 'sources' && <div className="result-collection"><div className="collection-heading"><span className="document-eyebrow">SOURCES & EVIDENCE</span><h1>研究来源 <small>{evidence.length}</small></h1><p>查看本次研究使用的网页、知识库摘录与业务查询快照。</p></div>{!evidence.length && <EmptyResult title="暂无来源记录">本次任务尚未保存检索来源。</EmptyResult>}<div className="source-catalog">{evidence.map(item => <button key={item.id} onClick={() => onEvidence(item)}><span className="source-index">{item.id}</span><div><small>{sourceLabels[item.source] || '公开网页'}</small><h3>{item.title}</h3><p>{item.content?.slice(0, 160)}</p></div><ArrowRightOutlined /></button>)}</div></div>}
      </>}
    </div>
  </section>
}
