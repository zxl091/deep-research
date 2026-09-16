import { Button, Checkbox, Empty, Popover, Select, Switch } from 'antd'
import { BookOutlined, DatabaseOutlined, GlobalOutlined, PlusOutlined } from '@ant-design/icons'
import type { KnowledgeBase } from '@/api/knowledge'

interface Props {
  sources: string[]; onToggle: (source: string, enabled: boolean) => void
  kbs: KnowledgeBase[]; kbIds: string[]; onKbsChange: (ids: string[]) => void
  useMemory: boolean; onMemoryChange: (enabled: boolean) => void; disabled: boolean
}
export default function SourcePicker(props: Props) {
  const { sources, onToggle, kbs, kbIds, onKbsChange, useMemory, onMemoryChange, disabled } = props
  const names: Record<string, string> = { web: '联网', local: '知识库', database: '数据库' }
  return <Popover trigger="click" placement="topLeft" overlayClassName="source-picker-popover" content={<div className="source-picker-content">
    <h3>本次研究的资料范围</h3><p>只使用你选中的资料来源。</p>
    <div className="source-picker-options">
      <Checkbox checked={sources.includes('web')} disabled={disabled} onChange={e => onToggle('web', e.target.checked)}><GlobalOutlined /> 联网搜索</Checkbox>
      <Checkbox checked={sources.includes('local')} disabled={disabled} onChange={e => onToggle('local', e.target.checked)}><BookOutlined /> 个人知识库</Checkbox>
      {sources.includes('local') && <Select aria-label="选择知识库" mode="multiple" placeholder="选择知识库" value={kbIds} onChange={onKbsChange} disabled={disabled} options={kbs.map(k => ({ label: `${k.name} · ${k.document_count} 份文档`, value: k.id }))} style={{ width: '100%' }} getPopupContainer={node => node.parentElement!} notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请先到知识库上传资料" />} />}
      <Checkbox checked={sources.includes('database')} disabled={disabled} onChange={e => onToggle('database', e.target.checked)}><DatabaseOutlined /> 业务数据库</Checkbox>
    </div><label className="source-memory-setting"><span>使用跨会话摘要</span><Switch size="small" checked={useMemory} disabled={disabled} onChange={onMemoryChange} /></label>
  </div>}><Button type="text" icon={<PlusOutlined />} disabled={disabled} aria-label="选择资料范围" className="source-picker-trigger">{sources.length ? sources.map(s => names[s]).join(' + ') : '选择资料'}</Button></Popover>
}
