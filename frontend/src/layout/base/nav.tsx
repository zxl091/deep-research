import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { EditOutlined, BookOutlined, HistoryOutlined, DatabaseOutlined, MenuFoldOutlined, MenuUnfoldOutlined, MoreOutlined } from '@ant-design/icons'
import { getSessions, type Session } from '@/api/session'
import './nav.scss'

const items = [
  { href: '/', label: '新的研究', short: '研究', icon: <EditOutlined /> },
  { href: '/knowledge', label: '知识库', short: '知识库', icon: <BookOutlined /> },
  { href: '/memory', label: '会话与记忆', short: '会话', icon: <HistoryOutlined /> },
  { href: '/database', label: '数据库探索', short: '数据库', icon: <DatabaseOutlined /> },
]

export function Nav({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { pathname } = useLocation()
  const [recent, setRecent] = useState<Session[]>([])
  const [failed, setFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let live = true
    getSessions({ limit: 15 }).then(r => { if (live) { setRecent(r.data); setFailed(false) } })
      .catch(() => { if (live) setFailed(true) })
    return () => { live = false }
  }, [pathname, retry])
  return <>
    <div className="sidebar-brand-row"><NavLink to="/" className="workspace-brand" title="DeepResearch"><span className="brand-symbol">dr</span><strong>DeepResearch</strong></NavLink><button className="sidebar-toggle" onClick={onToggle} title={collapsed ? '展开侧栏' : '收起侧栏'} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>{collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</button></div>
    <nav className="workspace-nav" aria-label="主要功能">{items.map(item => <NavLink key={item.href} to={item.href} title={item.label} end={item.href === '/'} className={({ isActive }) => `workspace-nav-item ${isActive || (item.href === '/' && pathname.startsWith('/chat')) ? 'is-active' : ''}`}>{item.icon}<span>{collapsed ? item.short : item.label}</span></NavLink>)}</nav>
    <section className="sidebar-recent"><div className="nav-section-label"><span>最近研究</span><NavLink to="/memory" title="全部会话" aria-label="查看全部会话"><MoreOutlined /></NavLink></div>
      {recent.map(session => <NavLink key={session.id} to={`/chat/${session.id}`} title={session.title} className={({ isActive }) => `recent-session ${isActive ? 'is-active' : ''}`}><span>{session.title}</span></NavLink>)}
      {failed ? <button className="sidebar-retry" onClick={() => setRetry(n => n + 1)}>加载失败，点击重试</button> : !recent.length && <p className="sidebar-empty">你的研究会保存在这里</p>}
    </section>
  </>
}
