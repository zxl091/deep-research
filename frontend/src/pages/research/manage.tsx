import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Empty, Input, Modal, Popconfirm, Spin, Tabs, Tag, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ArrowRightOutlined } from '@ant-design/icons'
import * as sessions from '@/api/session'
import * as assistant from '@/api/assistant'
import type { PersonalMemory } from '@/api/assistant'
import './workspace.scss'

export default function SessionMemoryPage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('sessions')
  const [records, setRecords] = useState<sessions.Session[]>([])
  const [memories, setMemories] = useState<PersonalMemory[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [editor, setEditor] = useState<{ type: 'memory' | 'session'; id?: string; value: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const load = async () => {
    setLoading(true)
    try {
      const [a, b] = await Promise.all([sessions.getSessions({ limit: 100 }), assistant.getMemories()])
      setRecords(a.data); setMemories(b.data)
    } catch { message.error('记录加载失败，请稍后重试') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  const save = async () => {
    if (!editor?.value.trim()) return
    setSaving(true)
    try {
      if (editor.type === 'memory') await assistant.saveMemory(editor.value.trim(), editor.id)
      else await sessions.updateSession(editor.id!, { title: editor.value.trim() })
      setEditor(null); await load(); message.success('已保存')
    } catch { message.error('保存失败') }
    finally { setSaving(false) }
  }
  const summarize = async (sessionId: string) => {
    setSaving(true)
    try { const response = await assistant.summarizeSession(sessionId); await load(); message.success(response.data.index_status === 'pending' ? '摘要已保存，向量索引待重试' : '记忆已整理') }
    catch { message.error('整理失败或任务仍在运行，请稍后重试') }
    finally { setSaving(false) }
  }
  const remove = async (id: string, type: string) => {
    try { if (type === 'memory') await assistant.removeMemory(id); else await sessions.deleteSession(id); await load() }
    catch { message.error('删除失败') }
  }
  return <main className="research-workspace"><header className="workspace-topbar"><span>个人工作空间 / 会话与记忆</span><Button icon={<PlusOutlined />} onClick={() => navigate('/')}>新的研究</Button></header>
    <div className="management-body"><span className="eyebrow">YOUR RESEARCH, CONTINUED</span><h1>让研究积累下来。</h1><p className="management-description">查看历史会话，以及自动提取的研究摘要、洞察和关注主题。</p>
      <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'sessions', label: `会话记录 · ${records.length}` }, { key: 'memories', label: `长期摘要 · ${memories.length}` }]} />
      <Input.Search aria-label="筛选记录" placeholder="搜索当前已加载的记录" value={search} onChange={e => setSearch(e.target.value)} allowClear style={{ maxWidth: 360, marginBottom: 24 }} />
      {loading ? <Spin /> : tab === 'sessions' ? <div className="management-list">{records.filter(s => s.title.includes(search)).map(s => <article key={s.id}><button className="record-open" onClick={() => navigate(`/chat/${s.id}`)}><h3>{s.title}</h3><p>{new Date(s.updated_at).toLocaleDateString('zh-CN')} · {s.message_count} 条消息</p></button><Button type="text" loading={saving} onClick={() => void summarize(s.id)}>整理记忆</Button><Button type="text" aria-label={`重命名 ${s.title}`} icon={<EditOutlined />} onClick={() => setEditor({ type: 'session', id: s.id, value: s.title })} /><Popconfirm title="删除这个会话及其研究记录？" onConfirm={() => remove(s.id, 'session')}><Button type="text" danger aria-label={`删除 ${s.title}`} icon={<DeleteOutlined />} /></Popconfirm><Button type="text" aria-label={`打开 ${s.title}`} icon={<ArrowRightOutlined />} onClick={() => navigate(`/chat/${s.id}`)} /></article>)}{!records.length && <Empty description="还没有会话，从一个问题开始吧" />}</div> : <><p className="memory-note">两轮完整问答或较长对话后自动生成摘要，保存在 PostgreSQL 并建立 Milvus 向量索引。新会话按相关性召回最多 3 条；摘要是历史背景，不是事实证据。</p><div className="memory-grid">{memories.filter(m => m.content.includes(search)).map(m => <article key={m.id}><Tag color={m.metadata?.index_status === 'ready' ? 'green' : 'orange'}>{m.metadata?.index_status === 'ready' ? '向量索引就绪' : '索引待重试'}</Tag><h3>{m.metadata?.source_title}</h3><p>{m.content}</p><p>关注主题：{m.metadata?.topics?.join("、") || "无"}</p><ul>{m.metadata?.insights?.map((text, i) => <li key={i}>{text}</li>)}</ul><div>{m.session_id && <Button type="text" onClick={() => navigate(`/chat/${m.session_id}`)}>来源会话</Button>}{m.session_id && <Button type="text" loading={saving} onClick={() => void summarize(m.session_id!)}>更新 / 重试索引</Button>}<Popconfirm title="删除后，后续研究将不再使用这条记忆。" onConfirm={() => remove(m.id, 'memory')}><Button type="text" danger icon={<DeleteOutlined />}>删除</Button></Popconfirm></div></article>)}</div>{!memories.length && <Empty description="尚无自动摘要。完成两轮问答后生成，也可在会话记录中点击整理记忆。" />}</>}
    </div><Modal title={editor?.type === 'session' ? '重命名会话' : '希望助手记住什么？'} open={!!editor} onCancel={() => setEditor(null)} onOk={() => void save()} confirmLoading={saving} okButtonProps={{ disabled: !editor?.value.trim() }} okText="保存" cancelText="取消"><Input.TextArea aria-label="编辑内容" value={editor?.value || ''} onChange={e => setEditor(old => old ? { ...old, value: e.target.value } : null)} autoSize={{ minRows: 3, maxRows: 10 }} maxLength={editor?.type === 'session' ? 100 : 4000} /></Modal></main>
}
