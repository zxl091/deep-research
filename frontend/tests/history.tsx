// Manual browser regression fixture. No real API calls, login or model costs.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import axios from 'axios'
import type { ResearchRun } from '../src/api/assistant'

const sid = 'history-fixture'
const firstQuestion = '请概括《华电科工.pdf》的主要内容，列出三条关键信息，并注明来源。'
const makeRun = (id: string, query: string, report: string, status = 'completed'): ResearchRun => ({
  id, session_id: sid, created_at: `2026-09-16T10:00:0${id}`, query, report, status,
  events: [{ seq: 1, type: 'completed', message: '测试记录已保存', time: '2026-09-16T10:00:00' }],
  state: { mode: 'answer', phase: 'finished', round: 1, scope: { sources: ['local'], kb_ids: ['fixture-kb'], use_memory: false },
    warnings: [], gaps: [], actions: [], usage: { model_calls: 1, tool_calls: 1, prompt_tokens: 10, completion_tokens: 10 },
    evidence: [{ id: 'E1', title: `第${id}轮引用`, content: `第${id}轮的独立来源内容`, url: 'local://kb/fixture/doc', source: 'knowledge' }] },
})
let runs = [makeRun('2', '其中有哪些风险？', '第二轮回答：这是风险说明。'), makeRun('1', firstQuestion, '第一轮完整回答：这是文档概括。')]
axios.defaults.adapter = async config => {
  let data: unknown
  const url = config.url || ''
  if (url === '/knowledge-bases') data = [{ id: 'fixture-kb', name: '练习库', document_count: 1 }]
  else if (url === '/assistant/runs' && config.method === 'post') {
    const body = JSON.parse(config.data)
    const next = makeRun(String(runs.length + 1), body.query, '', 'queued')
    runs = [next, ...runs]; data = next
  } else if (url === '/assistant/runs') data = runs.slice(Number(config.params?.offset || 0), Number(config.params?.offset || 0) + 30)
  else if (/\/assistant\/runs\/\d+\/cancel$/.test(url)) {
    const id = url.split('/').at(-2)
    runs = runs.map(r => r.id === id ? { ...r, status: 'cancelled' } : r)
    data = runs.find(r => r.id === id)
  } else if (/\/assistant\/runs\/\d+$/.test(url)) data = runs.find(r => r.id === url.split('/').at(-1))
  else if (url.includes('/sessions/')) data = { id: sid, title: firstQuestion, messages: [] }
  else throw new Error(`Unexpected mock request: ${config.method} ${url}`)
  return { data, status: 200, statusText: 'OK', config, headers: {} }
}
const originalFetch = window.fetch.bind(window)
window.fetch = (input, init) => {
  if (String(input).includes('/assistant/runs/') && String(input).includes('/events')) {
    return Promise.resolve(new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(': heartbeat\n\n'))
      init?.signal?.addEventListener('abort', () => controller.close(), { once: true })
    } }), { headers: { 'Content-Type': 'text/event-stream' } }))
  }
  return originalFetch(input, init)
}
const { default: Workspace } = await import('../src/pages/research/index')
createRoot(document.getElementById('root')!).render(<MemoryRouter initialEntries={[`/chat/${sid}`]}>
  <Routes><Route path="/chat/:id" element={<Workspace />} /></Routes>
</MemoryRouter>)
