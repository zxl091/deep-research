// Documentation-only fixture: real UI, synthetic data, no API/model requests.
// Open /tests/readme.html?view=home|report|memory on the Vite dev server.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import axios from 'axios'
import '@ant-design/v5-patch-for-react-19'
import 'normalize.css'
import '../src/antd.scss'
import '../src/index.css'
import type { ResearchRun } from '../src/api/assistant'

const sid = 'readme-demo'
const records = [
  { id: sid, title: '个人知识库：RAG 与长上下文怎么选？', message_count: 6 },
  { id: 'demo-2', title: '从四份研报中梳理项目与风险', message_count: 4 },
  { id: 'demo-3', title: '用自然语言探索业务数据', message_count: 2 },
].map(row => ({ ...row, session_type: 'deepsearch', created_at: '2026-09-16T02:00:00Z', updated_at: '2026-09-16T03:00:00Z' }))
const run: ResearchRun = {
  id: 'readme-run', session_id: sid, created_at: '2026-09-16T02:00:00Z', status: 'completed',
  query: '面向个人知识库，比较 RAG 与长上下文方案，给出选型建议和待验证问题。',
  report: `# 个人知识库，如何选择检索方案？

> 界面演示：以下是人工编写的示例内容，用于展示报告结构，不代表实际模型输出或评测结果。

## 核心结论

**先按资料规模与使用方式拆分问题，再决定是否引入检索。** 小范围、一次性的材料阅读，可以先验证直接提供上下文的方案；持续更新、反复查询的资料库，则需要重点验证检索覆盖率与引用定位能力。[E1]

## 方案对比

| 关注点 | RAG 检索增强 | 长上下文 |
| --- | --- | --- |
| 输入组织 | 先检索，再提供相关片段 | 将所选材料放入上下文 |
| 需要验证 | 切分、召回与排序是否漏掉证据 | 长材料中的证据定位是否稳定 |
| 资料更新 | 更新文档及对应索引 | 重新组织本次输入材料 |

## 下一步验证

使用同一批文档和问题，对比答案依据、遗漏信息及调用成本。先建立可复核的样本，再判断方案是否适合自己的资料库。[E2]`,
  events: ['研究规划：拆分选型问题', '资料检索：收集并整理来源', '分析：对齐比较维度', '写作：整合章节与引用', '审核：记录结论与待验证问题'].map((message, i) => ({ seq: i + 1, type: i === 4 ? 'completed' : 'stage', message, time: `2026-09-16T02:0${i}:00Z` })),
  state: {
    mode: 'research', engine: 'full_research_v2', phase: 'finished', round: 1,
    scope: { sources: ['web', 'local'], kb_ids: ['demo-kb'], use_memory: true },
    warnings: [], gaps: [], actions: [],
    usage: { model_calls: 0, tool_calls: 0, prompt_tokens: 0, completion_tokens: 0 },
    evidence: [
      { id: 'E1', title: '知识库方案比较笔记（示例文档）', source: 'knowledge', url: 'local://demo/notes', content: '演示材料：从资料规模、更新频率与引用需求三个维度组织选型问题。' },
      { id: 'E2', title: '检索方案验收清单（示例文档）', source: 'knowledge', url: 'local://demo/checklist', content: '演示材料：对比方案时使用同一批文档与问题，记录证据覆盖情况及调用成本。' },
    ],
    context_usage: { short_term: 'redis', recalled: 1, history_tokens: 0, summary_tokens: 0, long_term_enabled: true, references: [{ id: 'demo-memory', session_id: 'demo-2', title: '从四份研报中梳理项目与风险', score: 0 }] },
  },
}
const memories = [
  { id: 'demo-memory', session_id: sid, explicit: false, created_at: '2026-09-16T03:00:00Z',
    content: '本次会话比较了 RAG 与长上下文在个人知识库中的使用方式。后续研究需要围绕真实文档样本验证证据覆盖情况，而不是仅比较回答篇幅。',
    metadata: { index_status: 'ready', source_title: records[0].title, topics: ['知识库', 'RAG', '方案选型'], insights: ['用相同文档与问题比较两种方案。', '保留引用定位与失败样本，便于复核。'] } },
  { id: 'demo-memory-2', session_id: 'demo-2', explicit: false, created_at: '2026-09-16T02:00:00Z',
    content: '本次会话整理了研报阅读的分析框架：先列出项目与业务，再逐项核对风险依据。后续需补充原文页码与信息日期。',
    metadata: { index_status: 'ready', source_title: records[1].title, topics: ['研报阅读', '项目梳理', '风险证据'], insights: ['区分文档原始事实与研究推断。', '对缺少依据的结论保留待验证标记。'] } },
]
axios.defaults.adapter = async config => {
  if (config.method !== 'get') throw new Error('README 演示页为只读，不提交任何修改。')
  const url = config.url || ''
  let data: unknown
  if (url === '/sessions') data = records
  else if (url.startsWith('/sessions/')) data = { ...records[0], messages: [] }
  else if (url === '/knowledge-bases') data = [{ id: 'demo-kb', name: '研究资料库（示例）', document_count: 4 }]
  else if (url === '/assistant/runs') data = Number(config.params?.offset || 0) ? [] : [run]
  else if (url === '/assistant/runs/readme-run') data = run
  else if (url === '/assistant/memories') data = memories
  else throw new Error(`未配置的演示接口：${url}`)
  return { data, status: 200, statusText: 'OK', config, headers: {} }
}
const { BaseLayout } = await import('../src/layout/base/index')
const { default: Workspace } = await import('../src/pages/research/index')
const { default: Memories } = await import('../src/pages/research/manage')
const view = new URLSearchParams(location.search).get('view')
const entry = view === 'report' ? `/chat/${sid}` : view === 'memory' ? '/memory' : '/'
createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#387f70', borderRadius: 8 } }}>
    <MemoryRouter initialEntries={[entry]}><BaseLayout><Routes>
      <Route path="/" element={<Workspace />} /><Route path="/chat/:id" element={<Workspace />} /><Route path="/memory" element={<Memories />} />
    </Routes></BaseLayout></MemoryRouter>
    <div style={{ position: 'fixed', top: 14, right: 24, zIndex: 1000, color: '#746b51', background: '#fbf7e9', border: '1px solid #e8dec6', borderRadius: 6, padding: '5px 10px', fontSize: 12, pointerEvents: 'none' }}>界面演示 · 示例数据，非评测结果</div>
  </ConfigProvider>,
)
