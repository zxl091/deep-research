import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Button, ConfigProvider, Drawer, Input, Select, Spin, Tag, message } from 'antd'
import { ArrowUpOutlined, PlusOutlined, StopOutlined, DownloadOutlined, ReadOutlined, CheckCircleOutlined, MessageOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import * as sessions from '@/api/session'
import { getKnowledgeBases, type KnowledgeBase } from '@/api/knowledge'
import * as assistant from '@/api/assistant'
import type { ResearchRun, Evidence } from '@/api/assistant'
import { authState } from '@/store/auth'
import { openSource } from '@/utils/source-link'
import Report from './report'
import SourcePicker from './source-picker'
import ResultWorkbench from './result-workbench'
import ConversationTurn from './conversation-turn'
import { downloadText, reportStructure, statusLabels } from './result-utils'
import './workspace.scss'
import './workbench.scss'

const active = (r: ResearchRun | null) => !!r && ['queued', 'running'].includes(r.status)
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
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [mobilePane, setMobilePane] = useState<'conversation' | 'results'>('conversation')
  const conversation = useRef<HTMLDivElement>(null)
  const selectedRun = runs.find(item => item.id === selectedId) || run

  useEffect(() => { getKnowledgeBases().then(r => setKbs(r.data)).catch(() => message.error('知识库列表加载失败')) }, [])
  useEffect(() => {
    setSelectedId(null); setMobilePane('conversation'); setEvidence(null); setRun(null); setRuns([]); setHistory([]); setHasOlder(false); setConnectionError(''); setTitle(''); setLoading(false)
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
  useEffect(() => { conversation.current?.scrollTo(0, conversation.current.scrollHeight) }, [runId])
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
      setQuery(''); setSelectedId(null)
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
      setSelectedId(response.data.id)
      setRuns(old => old.map(r => r.id === response.data.id ? response.data : r))
    }
    catch { message.error('操作未完成，请刷新任务状态后重试') }
    finally { setBusy(false) }
  }

  const selectResult = (target: string) => { setSelectedId(target); setMobilePane('results') }
  const exportReport = () => {
    if (!selectedRun?.report) return
    const name = reportStructure(selectedRun.report).headings[0]?.title || selectedRun.query
    downloadText(selectedRun.report, `${name.slice(0, 80)}.md`)
  }

  return <ConfigProvider theme={{ token: { colorPrimary: '#2563eb', colorText: '#27272a', colorTextSecondary: '#71717a', colorBorder: '#e4e4e7' } }}><main className={`research-workspace research-studio mobile-${mobilePane}`}>
    <header className="studio-header">
      <a href="/" className="studio-brand"><ReadOutlined /><strong>DeepResearch</strong></a>
      <span className="studio-project" title={title}>{id ? title || '研究会话' : '个人研究工作台'}</span>
      <div className="studio-header-actions">{selectedRun && <span className={`studio-status status-${selectedRun.status}`}>{active(selectedRun) ? <Spin size="small" /> : selectedRun.status === 'completed' ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}{statusLabels[selectedRun.status]}</span>}<Button icon={<DownloadOutlined />} type="primary" disabled={!selectedRun?.report} onClick={exportReport} title="下载 Markdown 格式的研究报告">导出报告</Button></div>
    </header>
    <div className="mobile-pane-switch"><button className={mobilePane === 'conversation' ? 'active' : ''} onClick={() => setMobilePane('conversation')}><MessageOutlined />研究对话</button><button className={mobilePane === 'results' ? 'active' : ''} onClick={() => setMobilePane('results')}><ReadOutlined />研究成果</button></div>
    <div className="studio-body">
      <aside className="conversation-pane" aria-label="研究对话">
        <div className="conversation-pane-heading"><h1>研究对话</h1><Button type="text" aria-label="新的研究" title="新的研究" icon={<PlusOutlined />} onClick={() => navigate('/')} /></div>
        <div className="conversation-scroll" ref={conversation}>
          {connectionError && <Alert type="warning" message={connectionError} showIcon />}
          {loading && <div className="workspace-loading"><Spin /> 正在读取研究记录</div>}
          {!id && <section className="conversation-welcome"><span className="welcome-mark"><ReadOutlined /></span><h2>今天想研究什么？</h2><p>提出主题，选择资料。<br />把复杂问题整理成有依据的结论。</p><div className="welcome-examples">{examples.map((example, i) => <button key={example} onClick={() => { setQuery(example); setMode(i === 2 ? 'sql' : 'research'); setSources(i === 0 ? ['local'] : i === 2 ? ['database'] : ['web']) }}><PlusOutlined /><span>{['比较文档观点', '开展专题调研', '探索业务数据'][i]}</span></button>)}</div></section>}
          {hasOlder && <Button className="load-older-turns" size="small" loading={loadingOlder} onClick={() => void loadOlder()}>加载更早的问答</Button>}
          {[...runs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).map(turn => <ConversationTurn key={turn.id} run={turn} selected={selectedRun?.id === turn.id} busy={busy} running={running} onSelect={() => selectResult(turn.id)} onControl={(action, target) => void controlRun(action, target)} />)}
          {id && !run && !loading && !!history.length && <article className="legacy-history"><Tag>历史会话</Tag>{history.map(m => <section key={m.id}><h3>{m.role === 'user' ? '你的问题' : '助手回复'}</h3><Report content={m.content} /></section>)}</article>}
          {id && !run && !loading && !history.length && !connectionError && <div className="conversation-welcome"><h2>开始这次研究</h2><p>在下方输入主题，成果将在右侧呈现。</p></div>}
        </div>
        <div className="conversation-compose-area"><section className="research-composer">
          <Input.TextArea aria-label="研究问题" value={query} onChange={e => setQuery(e.target.value)} autoSize={{ minRows: 3, maxRows: 7 }} placeholder={id ? '继续追问，或提出新的研究要求…' : '描述你想研究的主题…'} disabled={running} maxLength={8000} onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') void submit() }} />
          <div className="composer-footer"><div className="composer-tools">
            <SourcePicker sources={sources} onToggle={toggleSource} kbs={kbs} kbIds={kbIds} onKbsChange={setKbIds} useMemory={useMemory} onMemoryChange={setUseMemory} disabled={running} />
            <Select aria-label="研究模式" value={mode} disabled={running} onChange={value => { setMode(value); if (value === 'sql') toggleSource('database', true) }} variant="borderless" options={[{ value: 'research', label: '深度研究' }, { value: 'auto', label: '自动识别' }, { value: 'answer', label: '简洁问答' }, { value: 'sql', label: '数据库查询' }]} />
          </div><div className="composer-submit"><Button type="primary" shape="circle" aria-label={running ? '停止研究' : id ? '发送追问' : '开始研究'} title={running ? '停止研究' : '发送问题（Ctrl + Enter）'} icon={running ? <StopOutlined /> : <ArrowUpOutlined />} loading={busy} disabled={!running && (!query.trim() || loading)} onClick={() => running ? void controlRun('cancel') : void submit()} /></div></div>
        </section><p className="composer-footnote">{running ? '研究在后台进行，切换页面不会中断' : 'Ctrl / ⌘ + Enter 发送 · 成果在右侧查看'}</p></div>
      </aside>
      <ResultWorkbench run={selectedRun} runs={runs} onSelect={selectResult} onEvidence={setEvidence} />
    </div>
    <Drawer title={evidence ? `[${evidence.id}] ${evidence.title}` : '研究证据'} open={!!evidence} onClose={() => setEvidence(null)} width={Math.min(640, window.innerWidth)}>
      {evidence && <><Tag>{evidence.source === 'database' ? '查询时的结果快照' : '检索原文摘录'}</Tag>{evidence.retrieved_at && <p>采集时间：{evidence.retrieved_at}</p>}{evidence.sql && <pre className="evidence-content">{evidence.sql}</pre>}<pre className="evidence-content">{evidence.content}</pre>{evidence.source !== 'database' && <Button onClick={() => openSource(evidence.url)}>打开来源</Button>}</>}
    </Drawer>
  </main></ConfigProvider>
}
