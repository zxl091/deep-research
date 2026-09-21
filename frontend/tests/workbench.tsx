// Development-only integration fixture: all API calls and SSE are local mocks.
// It neither modifies saved research nor consumes model quota.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import axios from 'axios'
import '@ant-design/v5-patch-for-react-19'
import 'normalize.css'
import '../src/index.css'
import type { ResearchRun } from '../src/api/assistant'

const sid = 'workbench-fixture'
const makeRun = (id: string, status = 'completed'): ResearchRun => ({
  id, session_id: sid, created_at: `2026-09-18T02:0${id}:00Z`, status,
  query: `第 ${id} 次离线研究：比较示例企业`,
  report: status === 'completed' ? `# 第 ${id} 次研究报告\n\n> 以下为离线测试资料，不代表真实企业。\n\n## 核心结论\n\n第 ${id} 次独立结果，示例甲高于示例乙。[E1]\n\n## 企业对比\n\n| 公司 | 指标 | 数值 |\n| --- | --- | --- |\n| 示例甲 | 测试量 | 12 |\n| 示例乙 | 测试量 | 8 |\n\n## 边界与说明\n\n<iframe src="https://example.org"></iframe>\n\n[不安全链接](javascript:alert(1))` : '',
  events: [{ seq: 1, type: 'stage', message: '离线测试：检索资料', time: '2026-09-18T02:01:00Z' }],
  state: { engine: 'full_research_v2', mode: 'research', phase: status === 'completed' ? 'finished' : 'searching', round: 1,
    scope: { sources: ['web'], kb_ids: [], use_memory: true }, warnings: [], gaps: [], actions: [],
    usage: { model_calls: 0, tool_calls: 0, prompt_tokens: 0, completion_tokens: 0 },
    outline: [{ id: 'intro', title: '核心结论' }, { id: 'compare', title: '企业对比' }],
    draft_sections: { intro: '## 已完成章节\n\n这是已保存的章节草稿。' },
    evidence: [{ id: 'E1', title: `第 ${id} 次测试来源`, source: 'web', url: 'https://example.org', content: `第 ${id} 次测试原文，示例甲12项，示例乙8项。` }],
    knowledge_graph: { nodes: [{ id: 'a', name: '示例甲', type: 'company' }, { id: 'b', name: '测试业务', type: 'product' }], edges: [{ source: 'a', target: 'b', relation: '提供' }] },
    charts: [{ id: `chart-${id}`, title: '示例业务对比（测试数据）', echarts_option: { xAxis: { type: 'category', data: ['示例甲', '示例乙'] }, yAxis: { type: 'value' }, series: [{ type: 'bar', data: [12, 8], itemStyle: { color: '#2d6348' } }] }, data_contract: { type: 'bar', points: [{ label: '示例甲', value: 12, unit: '项', period: '2025', value_kind: 'actual', source_url: 'https://example.org', quote: '示例甲12项' }, { label: '示例乙', value: 8, unit: '项', period: '2025', value_kind: 'forecast', source_url: 'https://example.org', quote: '示例乙8项（预测）' }] } }],
  },
})
let runs = [makeRun('2'), makeRun('1')]
const streams = new Map<string, ReadableStreamDefaultController<Uint8Array>>()
function update(id: string, status: string) {
  runs = runs.map(run => run.id === id ? { ...run, status, ...(status === 'completed' ? { report: makeRun(id).report } : {}) } : run)
  const controller = streams.get(id)
  if (controller) {
    controller.enqueue(new TextEncoder().encode('id: 2\nevent: update\ndata: {}\n\n'))
    if (status !== 'running') { controller.enqueue(new TextEncoder().encode('event: done\ndata: {}\n\n')); controller.close(); streams.delete(id) }
  }
}
axios.defaults.adapter = async config => {
  const url = config.url || ''
  let data: unknown
  if (url === '/knowledge-bases') data = [{ id: 'fixture-kb', name: '测试知识库', document_count: 1 }]
  else if (url === '/sessions' && config.method === 'post') data = { id: sid }
  else if (url === '/sessions') data = [{ id: sid, title: '离线工作台验收', session_type: 'deepsearch', created_at: '2026-09-18T02:00:00Z', updated_at: '2026-09-18T02:00:00Z' }]
  else if (url.startsWith('/sessions/')) data = { id: sid, title: '离线工作台验收（无真实请求）', messages: [] }
  else if (url === '/assistant/runs' && config.method === 'post') {
    const next = makeRun(String(runs.length + 1), 'running'); next.query = JSON.parse(config.data).query
    runs = [next, ...runs]; data = next
  } else if (url === '/assistant/runs') data = runs
  else if (/\/assistant\/runs\/\d+\/(cancel|resume)$/.test(url)) {
    const id = url.split('/').at(-2)!
    update(id, url.endsWith('cancel') ? 'cancelled' : 'running'); data = runs.find(r => r.id === id)
  } else if (/\/assistant\/runs\/\d+$/.test(url)) data = runs.find(r => r.id === url.split('/').at(-1))
  else throw new Error(`Unexpected fixture request: ${config.method} ${url}`)
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}
window.fetch = async (input, init) => {
  const id = /\/assistant\/runs\/(\d+)\/events/.exec(String(input))?.[1]
  if (!id) throw new Error(`Unexpected fixture fetch: ${String(input)}`)
  return new Response(new ReadableStream({ start(controller) {
    streams.set(id, controller)
    controller.enqueue(new TextEncoder().encode(': heartbeat\n\n'))
    init?.signal?.addEventListener('abort', () => { if (streams.get(id) === controller) { streams.delete(id); controller.close() } }, { once: true })
  } }), { headers: { 'Content-Type': 'text/event-stream' } })
}
const { default: Workspace } = await import('../src/pages/research/index')
const { BaseLayout } = await import('../src/layout/base')
createRoot(document.getElementById('root')!).render(<ConfigProvider theme={{ token: { colorPrimary: '#2d6348' } }}><MemoryRouter initialEntries={[`/chat/${sid}`]}><BaseLayout><Routes><Route path="/chat/:id" element={<Workspace />} /><Route path="/" element={<Workspace />} /></Routes></BaseLayout><div style={{ position: 'fixed', bottom: 3, right: 14, zIndex: 2000, display: 'flex', gap: 8, fontSize: 10 }}><button onClick={() => update(runs[0].id, 'completed')}>测试：完成最新任务</button><button onClick={() => update(runs[0].id, 'failed')}>测试：模拟失败</button></div></MemoryRouter></ConfigProvider>)
