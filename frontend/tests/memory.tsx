import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'

axios.defaults.adapter = async config => {
  let data: unknown
  if (config.url === '/assistant/memories') data = [{ id: 'fixture', session_id: 'source-session', content: '研究充电设备利用率，下一步比较分时电价方案。',
    metadata: { source_title: '隔离测试：充电研究', topics: ['充电设备', '分时电价'], insights: ['实施效果尚待验证'], index_status: 'ready' } }]
  else if (config.url?.includes('/sessions')) data = []
  else throw new Error('Unexpected fixture request')
  return { data, status: 200, statusText: 'OK', config, headers: {} }
}
const { default: Manage } = await import('../src/pages/research/manage')
createRoot(document.getElementById('root')!).render(<MemoryRouter><Manage /></MemoryRouter>)
