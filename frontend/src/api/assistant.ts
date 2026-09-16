import { request } from './request'

export interface Evidence { id: string; title: string; url: string; content: string; source: string; sql?: string; retrieved_at?: string }
export interface RunEvent { seq: number; type: string; message: string; time: string }
export interface ResearchRun {
  id: string; session_id: string; query: string; status: string; report: string; events: RunEvent[]; created_at: string
  state: {
    mode: string; phase: string; round: number; scope: { sources: string[]; kb_ids: string[]; use_memory: boolean }
    plan?: { goal: string; intent: string; questions: string[] }; evidence: Evidence[]; warnings: string[]; gaps: string[]
    usage: { model_calls: number; tool_calls: number; prompt_tokens: number; completion_tokens: number; elapsed_seconds?: number }
    actions: { tool: string; query: string; status: string; duration_ms?: number }[]; error?: string
    context_usage?: { short_term?: string; history_tokens?: number; summary_tokens?: number; memory_tokens?: number; recalled?: number; long_term_enabled?: boolean; warnings?: string[]; references?: { id: string; session_id: string; title: string; score: number }[] }
    memory_update?: { status?: string; index_status?: string; summarized_messages?: number }
    engine?: string
    outline?: { id: string; title: string; status?: string }[]
    draft_sections?: Record<string, string>
    charts?: { id?: string; title?: string; chart_type?: string; image_base64?: string; echarts_option?: Record<string, unknown>; verified_data?: boolean; data_contract?: { coverage_note?: string; points: { label: string; value: number; unit: string; period: string; value_kind: string; source_url: string; quote: string }[] } }[]
    review_history?: { overall_assessment?: { verdict?: string; quality_score?: number; summary?: string }; issues?: { description?: string; severity?: string }[] }[]
  }
}
export interface PersonalMemory { id: string; content: string; explicit: boolean; created_at: string; session_id?: string; token_count?: number; metadata?: { index_status?: string; source_title?: string; insights?: string[]; topics?: string[] } }
const options = { loading: false, cancelRepeat: false }
export const listRuns = (session_id: string, offset = 0) => request.get<ResearchRun[]>('/assistant/runs', { ...options, params: { session_id, offset } })
export const getRun = (id: string) => request.get<ResearchRun>(`/assistant/runs/${id}`, options)
export const createRun = (body: { session_id: string; query: string; mode: string; sources: string[]; kb_ids: string[]; use_memory: boolean }) => request.post<ResearchRun>('/assistant/runs', body, options)
export const cancelRun = (id: string) => request.post<ResearchRun>(`/assistant/runs/${id}/cancel`, {}, options)
export const resumeRun = (id: string) => request.post<ResearchRun>(`/assistant/runs/${id}/resume`, {}, options)
export const getMemories = () => request.get<PersonalMemory[]>('/assistant/memories', options)
export const saveMemory = (content: string, id?: string) => id ? request.put(`/assistant/memories/${id}`, { content }, options) : request.post('/assistant/memories', { content }, options)
export const removeMemory = (id: string) => request.delete(`/assistant/memories/${id}`, options)
export const summarizeSession = (sessionId: string) => request.post<{ status: string; index_status?: string }>(`/assistant/sessions/${sessionId}/memory`, {}, { ...options, timeout: 180000 })
