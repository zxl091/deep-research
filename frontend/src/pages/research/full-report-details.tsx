import { useEffect, useRef } from 'react'
import type { ResearchRun } from '@/api/assistant'
import Report from './report'
import { sourceLink } from '@/utils/source-link'
const valueKinds: Record<string, string> = { actual: '实际值', forecast: '预测值', target: '目标值' }

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

export default function FullReportDetails({ run }: { run: ResearchRun }) {
  if (run.state.engine !== 'full_research_v2') return null
  const { outline = [], draft_sections = {}, charts = [], review_history = [] } = run.state
  return <div className="full-report-details">
    <details open={!run.report}><summary>研究大纲与章节 · {outline.length}</summary>
      {outline.map(section => <details key={section.id} className="research-section-draft"><summary>{section.title}<small>{draft_sections[section.id] ? '已有草稿' : '待完成'}</small></summary>
        {draft_sections[section.id] ? <Report content={draft_sections[section.id]} /> : <p>本章节尚未完成撰写。</p>}
      </details>)}
    </details>
    {!!charts.length && <details open><summary>研究图表 · {charts.length}</summary>
      {charts.map((chart, i) => <figure key={chart.id || i}><figcaption>{chart.title || `图表 ${i + 1}`}</figcaption>
        {chart.image_base64 ? <img alt={chart.title || '研究图表'} src={chart.image_base64.startsWith('data:image/') ? chart.image_base64 : `data:image/png;base64,${chart.image_base64}`} style={{ maxWidth: '100%' }} /> : chart.echarts_option ? <ResearchChart option={chart.echarts_option} /> : <p>图表尚未生成可展示的数据。</p>}
        {chart.data_contract?.coverage_note && <p>{chart.data_contract.coverage_note}</p>}
        {chart.data_contract && <details><summary>图表数据与原文 · {chart.data_contract.points.length} 项</summary>
          <p>数值已匹配检索原句；统计口径及来源可靠性仍需结合原文判断。</p>
          {chart.data_contract.points.map((point, n) => <section key={n}><p><strong>{point.label}</strong> · {point.period} · {point.value} {point.unit} · {valueKinds[point.value_kind] || point.value_kind}</p><blockquote>{point.quote}</blockquote><a href={sourceLink(point.source_url) || undefined} target="_blank" rel="noopener noreferrer">查看数据来源</a></section>)}
        </details>}
      </figure>)}
    </details>}
    {!!review_history.length && <details><summary>报告审核 · {review_history.length} 次</summary>
      {review_history.map((review, i) => <section key={i}><p>第 {i + 1} 次：{review.overall_assessment?.verdict === 'pass' ? '模型审核通过' : '需要补充或修订'}{review.overall_assessment?.quality_score != null && `（${review.overall_assessment.quality_score}/10）`}</p><p>{review.overall_assessment?.summary}</p>
        <ul>{review.issues?.map((issue, n) => <li key={n}>{issue.description}</li>)}</ul></section>)}
      <p>模型审核和引用检查不能替代人工事实核对。</p>
    </details>}
  </div>
}
