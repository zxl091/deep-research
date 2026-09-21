import { useEffect, useRef } from 'react'
import type { ResearchRun } from '@/api/assistant'
import Report from './report'
import KnowledgeGraph from './knowledge-graph'
import { sourceLink } from '@/utils/source-link'
const valueKinds: Record<string, string> = { actual: '实际值', forecast: '预测值', target: '目标值' }
const chartIssues: Record<string, string> = { insufficient_data: '证据不足', table_parse_failed: '表格解析失败', render_failed: '绘图执行失败', extraction_failed: '模型抽取失败', source_missing: '缺少可定位原文', parse_failed: '原文结构尚未解析', period_ambiguous: '时间范围未核实', incompatible_scope: '统计口径不可合并', binding_failed: '对象与数值未绑定', conflicting_values: '同口径数值冲突' }
const qualifiers: Record<string, string> = { at_least: '≥', at_most: '≤', more_than: '>', less_than: '<', approximate: '≈' }
const chartTypes: Record<string, string> = { line: '时间序列', bar: '柱状图', horizontal_bar: '横向条形图', pie: '饼图', donut: '环形图', grouped_bar: '分组柱状图', stacked_bar: '堆叠柱状图', radar: '雷达图', scatter: '散点图', heatmap: '热力图' }

function ResearchChart({ option }: { option: Record<string, unknown> }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let disposed = false
    let chart: import('echarts').ECharts | undefined
    let observer: ResizeObserver | undefined
    void import('echarts').then(ec => {
      if (disposed || !ref.current) return
      chart = ec.init(ref.current)
      chart.setOption({ ...option, tooltip: { trigger: 'axis', renderMode: 'richText' } })
      observer = new ResizeObserver(() => chart?.resize())
      observer.observe(ref.current)
    })
    return () => { disposed = true; observer?.disconnect(); chart?.dispose() }
  }, [option])
  return <div ref={ref} style={{ height: 340, width: '100%' }} />
}

export default function FullReportDetails({ run, section = 'all' }: { run: ResearchRun; section?: 'all' | 'chapters' | 'charts' | 'review' | 'data' }) {
  if (run.state.engine !== 'full_research_v2') return null
  const { outline = [], draft_sections = {}, charts = [], review_history = [] } = run.state
  return <div className="full-report-details">
    {(section === 'all' || section === 'charts') && run.state.chart_repair && <p className="data-note">图表已单独更新，共 {charts.length} 张。报告正文与原审核结论保留原版本，图表现状以此处为准。</p>}
    {(section === 'all' || section === 'chapters') && <details open={!run.report}><summary>研究大纲与章节 · {outline.length}</summary>
      {outline.map(section => <details key={section.id} className="research-section-draft"><summary>{section.title}<small>{draft_sections[section.id] ? '已有草稿' : '待完成'}</small></summary>
        {draft_sections[section.id] ? <Report content={draft_sections[section.id]} /> : <p>本章节尚未完成撰写。</p>}
      </details>)}
    </details>}
    {(section === 'all' || section === 'charts') && !!charts.length && <details open><summary>研究图表 · {charts.length}</summary>
      {charts.map((chart, i) => <figure key={chart.id || i}><figcaption>{chart.title || `图表 ${i + 1}`}</figcaption>
        {chart.data_contract && <p>{chartTypes[chart.data_contract.type || chart.chart_type || ''] || '研究图表'} · {chart.data_contract.points.length} 个原文数据点</p>}
        {chart.image_base64 ? <img alt={chart.title || '研究图表'} src={chart.image_base64.startsWith('data:image/') ? chart.image_base64 : `data:image/png;base64,${chart.image_base64}`} style={{ maxWidth: '100%' }} /> : chart.echarts_option ? <ResearchChart option={chart.echarts_option} /> : <p>图表尚未生成可展示的数据。</p>}
        {chart.data_contract?.coverage_note && <p>{chart.data_contract.coverage_note}</p>}
        {chart.data_contract?.display_note && <p>{chart.data_contract.display_note}</p>}
        {chart.data_contract && <details><summary>图表数据与原文 · {chart.data_contract.points.length} 项</summary>
          <p>新生成图表会核对对象、指标、期间与数值的对应关系；未知口径保留缺口，来源可靠性仍需人工判断。</p>
          {chart.data_contract.points.map((point, n) => <section key={n}><p><strong>{point.label}</strong>{point.series ? ` / ${point.series}` : ''} · {point.period} · {qualifiers[point.qualifier || ''] || ''}{point.value} {point.unit} · {valueKinds[point.value_kind] || point.value_kind}</p><blockquote>{point.quote}{point.context_quote && <><br />表头／上下文：{point.context_quote}</>}</blockquote>{point.observation && <p>证据绑定：{point.observation.entity} · {point.observation.metric} · {point.observation.period.start} 至 {point.observation.period.end}<br />统计口径：{point.observation.statistical_scope || '原文未明确'}；统计方式：{point.observation.aggregation || '原文未明确'}</p>}{point.period_note && <p>期间说明：{point.period_note}</p>}{point.conversion_note && <p>单位换算：{point.conversion_note}</p>}<a href={sourceLink(point.source_url) || undefined} target="_blank" rel="noopener noreferrer">查看数据来源</a></section>)}
        </details>}
      </figure>)}
    </details>}
    {section === 'all' && <KnowledgeGraph graph={run.state.knowledge_graph} />}
    {(section === 'all' || section === 'charts' || section === 'data') && !!run.state.chart_evidence_records?.records?.length && <details open={section === 'data' || !charts.length}><summary>核验数据台账 · {run.state.chart_evidence_records.records.length} 条来源记录</summary>
      <p>单点数据也会保留。只有同来源、同口径且时间可比较的数据才会被选入图表；仅标注年份的数据不代表全年统计。</p>
      <div className="data-table-scroll"><table><thead><tr><th>对象 / 指标</th><th>数值</th><th>时间与口径</th><th>证据</th></tr></thead><tbody>
        {run.state.chart_evidence_records.records.map(({ id, point: p }) => <tr key={id}><td>{p.entity}<br />{p.metric}</td><td>{qualifiers[p.qualifier || ''] || ''}{p.value} {p.unit}<br />{valueKinds[p.value_kind]}</td><td>{p.period} · {p.period_basis}<br />{p.scope}{run.state.chart_evidence_records?.table_only?.includes(id) && <><br />仅保留为表格</>}</td><td><details><summary>查看原句</summary><blockquote>{p.quote}</blockquote>{p.period_note && <p>{p.period_note}</p>}<a href={sourceLink(p.source_url) || undefined} target="_blank" rel="noopener noreferrer">来源</a></details></td></tr>)}
      </tbody></table></div>
    </details>}
    {(section === 'all' || section === 'charts') && !!run.state.chart_validation?.items?.some(item => Object.prototype.hasOwnProperty.call(chartIssues, item.status)) && <details><summary>图表待处理项</summary>
      {run.state.chart_validation.items.filter(item => Object.prototype.hasOwnProperty.call(chartIssues, item.status)).map(item => <p key={item.plan_id}>{item.title} · {chartIssues[item.status]}：{item.errors?.map(message => message.replace(/\[[a-z_]+:[^\]]+\]\s*/g, '')).join('；') || '未取得足够的可核验数据'}</p>)}
    </details>}
    {(section === 'all' || section === 'review') && !!review_history.length && <details><summary>报告审核 · {review_history.length} 次</summary>
      {review_history.map((review, i) => <section key={i}><p>第 {i + 1} 次：{review.overall_assessment?.verdict === 'pass' ? '模型审核通过' : '需要补充或修订'}{review.overall_assessment?.quality_score != null && `（${review.overall_assessment.quality_score}/10）`}</p><p>{review.overall_assessment?.summary}</p>
        <ul>{review.issues?.map((issue, n) => <li key={n}>{issue.description}</li>)}</ul></section>)}
      <p>模型审核和引用检查不能替代人工事实核对。</p>
    </details>}
  </div>
}
