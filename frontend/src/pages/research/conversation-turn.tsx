import { Button, Alert } from 'antd'
import { UserOutlined, ReadOutlined, ArrowRightOutlined, DownOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import type { ResearchRun } from '@/api/assistant'
import Report from './report'
import { runActive, statusLabels } from './result-utils'

export default function ConversationTurn({ run, selected, busy, running, onSelect, onControl }: {
  run: ResearchRun; selected: boolean; busy: boolean; running: boolean; onSelect: () => void; onControl: (action: 'cancel' | 'resume', run: ResearchRun) => void
}) {
  const navigate = useNavigate()
  const inProgress = runActive(run)
  const isReport = run.state.engine === 'full_research_v2'
  const context = run.state.context_usage
  return <section className={`sidebar-turn status-${run.status} ${selected ? 'is-selected' : ''}`} aria-label={`问答：${run.query}`}>
    <div className="turn-speaker"><span><UserOutlined /></span><strong>你</strong><time>{new Date(run.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></div>
    <div className="turn-question">{run.query}</div>
    <div className="turn-speaker assistant-speaker"><span><ReadOutlined /></span><strong>DeepResearch</strong></div>
    {!isReport && run.report ? <div className="turn-answer"><Report content={run.report} /></div> : <p className="turn-reply">{inProgress ? '正在整理资料，已完成的内容会更新到右侧。' : run.report ? '本次研究已整理，可在右侧查看报告及相关成果。' : '本次研究尚未完成，可查看已有成果与执行记录。'}</p>}
    <details className="process-panel" open={inProgress}><summary className="process-heading"><span className={inProgress ? 'live-dot' : 'idle-dot'} /><span>研究过程</span><span>{statusLabels[run.status]}</span><DownOutlined /></summary>
      <ol className="process-timeline">{run.events.map(event => <li key={event.seq}><span>{event.message}</span><time>{new Date(event.time + (event.time.endsWith('Z') ? '' : 'Z')).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time></li>)}</ol>
      <div className="run-metrics"><div><b>{run.state.usage.tool_calls}</b><span>工具调用</span></div><div><b>{run.state.usage.model_calls}</b><span>模型调用</span></div><div><b>{run.state.usage.prompt_tokens + run.state.usage.completion_tokens}</b><span>Token</span></div></div>
      {!!run.state.warnings.length && <Alert type="warning" message={run.state.warnings.join('；')} />}
    </details>
    {run.state.error && <p className="turn-error" role="alert">{run.state.error}</p>}
    <button className="turn-result-link" onClick={onSelect}><ReadOutlined /><span>{selected ? '正在右侧查看' : '查看本次成果'}</span><ArrowRightOutlined /></button>
    <div className="run-controls">{inProgress && <Button size="small" icon={<StopOutlined />} loading={busy} onClick={() => onControl('cancel', run)}>停止研究</Button>}{['failed', 'interrupted'].includes(run.status) && <Button size="small" icon={<ReloadOutlined />} disabled={running || busy} onClick={() => onControl('resume', run)}>从已完成步骤继续</Button>}</div>
    {context && <details className="turn-memory"><summary>本轮记忆 · 召回 {context.recalled || 0} 条摘要</summary><p>近期对话：{context.short_term === 'redis' ? 'Redis 缓存' : '从数据库恢复'}，约 {context.history_tokens || 0} Token；当前会话摘要约 {context.summary_tokens || 0} Token。</p><p>跨会话检索：{context.long_term_enabled ? '已开启' : '已关闭'}。Token 为估算值。</p>{context.warnings?.map(text => <p key={text}>{text}</p>)}{context.references?.map(item => <button key={item.id} onClick={() => navigate(`/chat/${item.session_id}`)}>{item.title}</button>)}{run.state.memory_update && <p>摘要整理：{run.state.memory_update.status === 'saved' ? (run.state.memory_update.index_status === 'ready' ? '已保存并建立向量索引' : '已保存，索引待重试') : run.state.memory_update.status === 'failed' ? '失败，原始对话保留' : '暂无足够的新消息'}</p>}</details>}
  </section>
}
