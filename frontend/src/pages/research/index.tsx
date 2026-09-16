import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, Drawer, Input, Select, Spin, Tag, message } from 'antd'
import { ArrowUpOutlined, FileTextOutlined, PlusOutlined, StopOutlined, ReloadOutlined, DownOutlined } from '@ant-design/icons'
import * as sessions from '@/api/session'
import { getKnowledgeBases, type KnowledgeBase } from '@/api/knowledge'
import * as assistant from '@/api/assistant'
import type { ResearchRun, Evidence } from '@/api/assistant'
import { authState } from '@/store/auth'
import { openSource } from '@/utils/source-link'
import Report from './report'
import SourcePicker from './source-picker'
import FullReportDetails from './full-report-details'
import './workspace.scss'

const active = (r: ResearchRun | null) => !!r && ['queued', 'running'].includes(r.status)
const labels: Record<string, string> = { queued: '等待开始', running: '正在研究', completed: '已完成', partial: '部分完成', cancelled: '已取消', interrupted: '运行中断', failed: '执行失败' }
const examples = ['比较两份文档的核心观点，标出共识与分歧', '比较 RAG 与长上下文两种知识库问答方案，分析适用场景、优势与限制，并给出选择建议', '统计业务数据的变化趋势，并解释查询口径']

export default function ResearchWorkspace() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('research')
  const [sources, setSources] = useState<string[]>(['web'])
  const [kbIds, setKbIds] = useState<string[]>([])
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [useMemory, setUseMemory] = useState(true)
  const [run, setRun] = useState<ResearchRun | null>(null)
  const [runs, setRuns] = useState<ResearchRun[]>([])
  const [hasOlder, setHasOlder] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [history, setHistory] = useState<sessions.Message[]>([])
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [connectionError, setConnectionError] = useState('')
  const [evidence, setEvidence] = useState<Evidence | null>(null)

  useEffect(() => { getKnowledgeBases().then(r => setKbs(r.data)).catch(() => message.error('知识库列表加载失败')) }, [])
  useEffect(() => {
    setRun(null); setRuns([]); setHistory([]); setHasOlder(false); setConnectionError(''); setTitle(''); setLoading(false)
    if (!id) return
    let live = true
    setLoading(true)
    Promise.all([assistant.listRuns(id), sessions.getSession(id)]).then(([research, session]) => {
      if (!live) return
      setRuns(research.data); setRun(research.data.find(active) || research.data[0] || null); setHasOlder(research.data.length === 30); setHistory(session.data.messages); setTitle(session.data.title)
      if (research.data[0]) {
        const scope = research.data[0].state.scope
        setSources(scope.sources); setKbIds(scope.kb_ids); setUseMemory(scope.use_memory); setMode(research.data[0].state.mode)
      }
    }).catch(() => { if (live) setConnectionError('会话加载失败，请检查连接或从会话列表重新打开。') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [id])

  // 事件流只负责订阅。断开、切换页面均不会取消服务端任务。
  const runId = run?.id
  const running = active(run)
  useEffect(() => {
    if (!runId || !running) return
    const controller = new AbortController()
    let cursor = 0
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      const response = await assistant.getRun(runId)
      if (!controller.signal.aborted) {
        setRun(response.data)
        setRuns(old => [response.data, ...old.filter(r => r.id !== runId)])
      }
      return response.data
    }
    const connect = async () => {
      try {
        const base = (import.meta.env.VITE_API_BASE || '').replace(/\/+$/, '')
        const response = await fetch(`${base}/assistant/runs/${runId}/events?after=${cursor}`, {
          headers: { Authorization: `Bearer ${authState.token}` }, signal: controller.signal,
        })
        if (!response.ok || !response.body) throw new Error('连接失败')
        setConnectionError('')
        const reader = response.body.getReader(), decoder = new TextDecoder()
        let buffer = ''
        while (!controller.signal.aborted) {
          const chunk = await reader.read()
          if (chunk.done) break
          buffer += decoder.decode(chunk.value, { stream: true })
          let split: number
          while ((split = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, split); buffer = buffer.slice(split + 2)
            const seq = /^id: (\d+)/m.exec(frame)
            if (seq) cursor = Number(seq[1])
            if (seq || frame.includes('event: done')) await refresh()
          }
        }
        if (!controller.signal.aborted && active(await refresh())) timer = setTimeout(connect, 1500)
      } catch {
        if (!controller.signal.aborted) {
          setConnectionError('进度连接暂时中断，正在重连；研究任务仍在后台运行。')
          timer = setTimeout(connect, 3000)
        }
      }
    }
    void connect()
    // 进程重启时代理连接可能没有及时关闭；定期校准终态，避免页面长期停在旧进度。
    let polling = false
    const poll = setInterval(async () => {
      if (polling || controller.signal.aborted) return
      polling = true
      try { await refresh() } catch { /* 事件流负责显示断线提示并重连 */ }
      finally { polling = false }
    }, 10000)
    return () => { controller.abort(); clearTimeout(timer); clearInterval(poll) }
  }, [runId, running])

  const submit = async () => {
    if (!query.trim() || busy || running) return
    if (sources.includes('local') && !kbIds.length) { message.warning('请选择要使用的知识库'); return }
    if (mode === 'sql' && !sources.includes('database')) { message.warning('请启用业务数据库'); return }
    setBusy(true)
    try {
      let sessionId = id
      if (!sessionId) sessionId = (await sessions.createSession({ title: '新的研究', session_type: 'deepsearch' })).data.id
      const result = await assistant.createRun({ session_id: sessionId, query: query.trim(), mode, sources, kb_ids: kbIds, use_memory: useMemory })
      setQuery('')
      if (id !== sessionId) navigate(`/chat/${sessionId}`)
      else { setRun(result.data); setRuns(old => [result.data, ...old]) }
    } catch { message.error('任务未能启动，请检查资料选择与连接后重试') }
    finally { setBusy(false) }
  }
  const toggleSource = (source: string, enabled: boolean) => setSources(old => enabled ? [...new Set([...old, source])] : old.filter(s => s !== source))
  const loadOlder = async () => {
    if (!id || loadingOlder) return
    setLoadingOlder(true)
    try {
      const response = await assistant.listRuns(id, runs.length)
      setRuns(old => [...old, ...response.data.filter(r => !old.some(existing => existing.id === r.id))])
      setHasOlder(response.data.length === 30)
    } catch { message.error('更早的问答加载失败，请重试') }
    finally { setLoadingOlder(false) }
  }
  const controlRun = async (action: 'cancel' | 'resume', target = run) => {
    if (!target) return
    setBusy(true)
    try {
      const response = await (action === 'cancel' ? assistant.cancelRun(target.id) : assistant.resumeRun(target.id))
      setRun(response.data)
      setRuns(old => old.map(r => r.id === response.data.id ? response.data : r))
    }
    catch { message.error('操作未完成，请刷新任务状态后重试') }
    finally { setBusy(false) }
  }

  return <main className="research-workspace">
    <header className="workspace-topbar"><span className="topbar-title" title={title}>{id ? title || '研究会话' : '新研究'}</span><Button icon={<PlusOutlined />} onClick={() => navigate('/')}>新的研究</Button></header>
    <div className={`workspace-body ${run ? 'has-run' : ''} ${id ? 'is-session' : 'is-home'}`}>
      {!id && <section className="research-hero"><h1>今天想研究什么？</h1><p>查阅资料、比较观点，得到有来源的结论。</p></section>}
      {connectionError && <Alert type="warning" message={connectionError} showIcon style={{ marginBottom: 16 }} />}
      {loading && <div className="workspace-loading"><Spin /> 正在读取研究记录</div>}
      {hasOlder && <Button className="load-older-turns" loading={loadingOlder} onClick={() => void loadOlder()}>加载更早的问答</Button>}
      {[...runs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).map(turn => {
        const run = turn
        const turnRunning = active(run)
        return <section className="run-layout conversation-turn" key={run.id} aria-label={`问答：${run.query}`}>
        <div className="user-question"><h2>{run.query}</h2></div>
        <details className="process-panel" open={turnRunning}><summary className="process-heading"><span className={turnRunning ? 'live-dot' : 'idle-dot'} /><span>研究过程</span><span>第 {run.state.round} 轮</span><DownOutlined /></summary>
          <ol className="process-timeline">{run.events.map(event => <li key={event.seq}><span>{event.message}</span><time>{new Date(event.time + (event.time.endsWith('Z') ? '' : 'Z')).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></li>)}</ol>
          <div className="run-metrics"><div><b>{run.state.usage.tool_calls}</b><span>工具调用</span></div><div><b>{run.state.usage.model_calls}</b><span>模型调用</span></div><div><b>{run.state.usage.prompt_tokens + run.state.usage.completion_tokens}</b><span>Token</span></div></div>
          {!!run.state.warnings.length && <Alert type="warning" message={run.state.warnings.join('；')} />}
        </details>
        <article className="report-panel">
          <div className="report-heading"><Tag>{labels[run.status] || run.status}</Tag>{run.state.engine === 'full_research_v2' && <Tag>完整研究报告</Tag>}{run.state.plan?.intent === 'recall' && <Tag>历史回顾</Tag>}
          </div>
          {run.report ? <Report content={run.report} /> : <div className="research-pending">{turnRunning ? <Spin size="small" /> : <FileTextOutlined />}<span>{run.state.error || (turnRunning ? '正在整理资料，完成后将在这里呈现结论。' : '本次任务尚未生成报告。')}</span>
            {run.state.plan?.questions && <ul>{run.state.plan.questions.map((q, i) => <li key={i}>{q}</li>)}</ul>}</div>}
          <FullReportDetails run={run} />
          {run.state.context_usage && <details className="evidence-list"><summary>本轮记忆 · 召回 {run.state.context_usage.recalled || 0} 条旧会话摘要</summary>
            <p>近期对话：{run.state.context_usage.short_term === 'redis' ? 'Redis 缓存' : '从数据库恢复'}，约 {run.state.context_usage.history_tokens || 0} Token；当前会话摘要约 {run.state.context_usage.summary_tokens || 0} Token。</p>
            <p>跨会话检索：{run.state.context_usage.long_term_enabled ? '已开启' : '已关闭'}。Token 使用 cl100k_base 估算，不等于模型账单用量。</p>
            {run.state.context_usage.warnings?.map(text => <p key={text}>{text}</p>)}
            {run.state.context_usage.references?.map(item => <p key={item.id}><Button type="link" onClick={() => navigate(`/chat/${item.session_id}`)}>{item.title}</Button>相关度 {item.score}</p>)}
            {run.state.memory_update && <p>摘要整理：{run.state.memory_update.status === 'saved' ? (run.state.memory_update.index_status === 'ready' ? '已保存并建立向量索引' : '已保存，索引待重试') : run.state.memory_update.status === 'failed' ? '失败，原始对话保留' : '暂无足够的新消息'}</p>}
          </details>}
          <div className="run-controls">{turnRunning && <Button icon={<StopOutlined />} loading={busy} onClick={() => void controlRun('cancel', run)}>停止研究</Button>}
            {['failed', 'interrupted'].includes(run.status) && <Button icon={<ReloadOutlined />} disabled={running || busy} loading={busy} onClick={() => void controlRun('resume', run)}>从已完成步骤继续</Button>}</div>
          {!!run.state.evidence.length && <details className="evidence-list"><summary>查看来源 <span>{run.state.evidence.length}</span></summary>{run.state.evidence.map(e => <button key={e.id} className="evidence-row" onClick={() => setEvidence(e)}><span className="evidence-id">{e.id}</span><div><strong>{e.title}</strong><small>{e.source === 'knowledge' ? '个人知识库' : e.source === 'database' ? '业务查询快照' : '公开网页'}</small></div><span>↗</span></button>)}</details>}
        </article>

      </section>})}
      {id && !run && !loading && !!history.length && <article className="report-panel legacy-history"><Tag>历史会话</Tag>{history.map(m => <section key={m.id}><h3>{m.role === 'user' ? '你的问题' : '助手回复'}</h3><Report content={m.content} /></section>)}</article>}
      <section className="research-composer">
        <Input.TextArea aria-label="研究问题" value={query} onChange={e => setQuery(e.target.value)} autoSize={{ minRows: 2, maxRows: 8 }} placeholder={id ? '围绕当前结论继续提问，或补充研究要求…' : '提出一个问题，或描述你想了解的主题…'} disabled={running} maxLength={8000} onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') void submit() }} />
        <div className="composer-footer"><div className="composer-tools">
          <SourcePicker sources={sources} onToggle={toggleSource} kbs={kbs} kbIds={kbIds} onKbsChange={setKbIds} useMemory={useMemory} onMemoryChange={setUseMemory} disabled={running} />
          <Select aria-label="研究模式" value={mode} disabled={running} onChange={value => { setMode(value); if (value === 'sql') toggleSource('database', true) }} variant="borderless" options={[{ value: 'research', label: '深度研究' }, { value: 'auto', label: '自动识别' }, { value: 'answer', label: '简洁问答' }, { value: 'sql', label: '数据库查询' }]} />
        </div><div className="composer-submit"><small>⌘ / Ctrl ↵</small><Button type="primary" shape="circle" aria-label={running ? '停止研究' : id ? '发送追问' : '开始研究'} title={running ? '停止研究' : '发送问题'} icon={running ? <StopOutlined /> : <ArrowUpOutlined />} loading={busy} disabled={!running && (!query.trim() || loading)} onClick={() => running ? void controlRun('cancel') : void submit()} /></div></div>
      </section>
      {!run && mode === 'research' && <p className="research-mode-hint">完整研究：专题检索、数据分析、分章写作与审核修订，不设总时长上限，可随时手动停止。</p>}
      {!id && <div className="research-examples">{examples.map((example, i) => <button key={example} onClick={() => { setQuery(example); setMode(i === 2 ? 'sql' : 'research'); setSources(i === 0 ? ['local'] : i === 2 ? ['database'] : ['web']) }}><PlusOutlined />{['比较文档', '调研专题', '查询数据'][i]}</button>)}</div>}
    </div>
    <Drawer title={evidence ? `[${evidence.id}] ${evidence.title}` : '研究证据'} open={!!evidence} onClose={() => setEvidence(null)} width={640}>
      {evidence && <><Tag>{evidence.source === 'database' ? '查询时的结果快照' : '检索原文摘录'}</Tag>{evidence.retrieved_at && <p>采集时间：{evidence.retrieved_at}</p>}{evidence.sql && <pre className="evidence-content">{evidence.sql}</pre>}<pre className="evidence-content">{evidence.content}</pre>{evidence.source !== 'database' && <Button onClick={() => openSource(evidence.url)}>打开来源</Button>}</>}
    </Drawer>
  </main>
}
