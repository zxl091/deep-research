import { Footer } from './footer'
import './index.scss'
import { Nav } from './nav'
import { useState } from 'react'

export function BaseLayout({ children }: { children?: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div className={`base-layout ${collapsed ? 'sidebar-collapsed' : ''}`}>
      <div className="base-layout__sidebar">
        <div className="base-layout__sidebar-main scrollbar-style">
          <Nav collapsed={collapsed} onToggle={() => setCollapsed(value => !value)} />

          <Footer />
        </div>
      </div>

      <div className="base-layout__content">{children}</div>
    </div>
  )
}
