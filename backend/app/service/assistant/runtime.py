"""单进程后台运行时。步骤提交后可恢复；HTTP 订阅者不拥有任务生命周期。"""
import asyncio
import copy
import time
import logging
from datetime import datetime
from uuid import UUID
from core.database import SessionLocal
from models.assistant import AssistantRun
from models.chat import ChatMessage, ChatSession
from .llm import ModelGateway, ModelQuotaExhausted, ModelCallTimedOut, ModelResponseError
from .context import load_context, compress_history
from .controller import plan_task, assess_gaps, write_report, requires_evidence
from .tools import TOOLS, execute_tool, evidence_key

ACTIVE = {'queued', 'running'}
tasks = {}


def run_time_limit(state):
    """完整研究无总时长上限，包括恢复旧的 30 分钟检查点。"""
    if state.get('engine') == 'full_research_v2' or (
        state.get('workflow_version') == 2 and state.get('mode') in ('research', 'auto')
    ):
        return None
    return state['budget'].get('max_seconds', 420)


def snapshot(run):
    public_state = {key: value for key, value in run.state.items()
                    if key not in ('deep_state', 'source_cache', 'source_snapshots')}
    return {'id': str(run.id), 'session_id': str(run.session_id), 'query': run.query, 'status': run.status,
            'state': public_state, 'events': run.events, 'report': run.report,
            'created_at': run.created_at.isoformat(), 'updated_at': run.updated_at.isoformat()}


def recover_interrupted():
    with SessionLocal() as db:
        for run in db.query(AssistantRun).filter(AssistantRun.status.in_(ACTIVE)):
            run.status = 'interrupted'
        db.commit()


def launch(run_id):
    key = str(run_id)
    if key not in tasks or tasks[key].done():
        tasks[key] = asyncio.create_task(execute(key))
        tasks[key].add_done_callback(lambda task: tasks.pop(key, None) if tasks.get(key) is task else None)


def save(run_id, state, kind, message, status='running', report=None):
    with SessionLocal() as db:
        run = db.get(AssistantRun, UUID(run_id))
        if not run or run.status == 'cancelled':
            raise asyncio.CancelledError()
        event = {'seq': len(run.events) + 1, 'type': kind, 'message': message, 'time': datetime.utcnow().isoformat()}
        run.state = copy.deepcopy(state)
        run.events = [*run.events, event]
        run.status = status
        if report is not None:
            run.report = report
            # 结果与终态在同一事务写入，恢复时不会重复保存助手消息。
            existing = db.query(ChatMessage).filter(ChatMessage.session_id == run.session_id,
                ChatMessage.role == 'assistant', ChatMessage.references_data['run_id'].astext == str(run_id)).first()
            if existing:
                existing.content = report
                existing.references_data = {'run_id': str(run_id), 'evidence': state['evidence']}
            else:
                db.add(ChatMessage(session_id=run.session_id, role='assistant', content=report,
                                   references_data={'run_id': str(run_id), 'evidence': state['evidence']}))
            session = db.get(ChatSession, run.session_id)
            session.updated_at = datetime.utcnow()
        db.commit()
        if report is not None:
            try:
                from .memory_store import cache_client, cache_key
                key = cache_key(run.user_id, run.session_id)
                cache_client().delete(key, key + ':head')
            except Exception:
                pass


async def execute(run_id):
    with SessionLocal() as db:
        run = db.get(AssistantRun, UUID(run_id))
        if not run or run.status not in ACTIVE:
            return
        state = copy.deepcopy(run.state)
        query, uid, sid = run.query, str(run.user_id), str(run.session_id)
    started = time.monotonic()
    previous_elapsed = state['usage'].get('elapsed_seconds', 0)
    limit = run_time_limit(state)
    state['budget']['max_seconds'] = limit
    model = ModelGateway(state['usage'])
    def commit(kind, message, status='running', report=None):
        state['usage']['elapsed_seconds'] = round(previous_elapsed + time.monotonic() - started, 2)
        save(run_id, state, kind, message, status, report)
    async def workflow():
        if state.get('engine') == 'full_research_v2':
            from .full_research import run_full_research
            await run_full_research(state, query, uid, sid, model, commit)
            return
        context = await asyncio.to_thread(load_context, sid, uid, state['scope']['use_memory'], query)
        state['context_usage'] = context.get('diagnostics', {})
        state['memory_ids'] = [m['id'] for m in context['memories']]
        if 'plan' not in state:
            commit('planning', '正在识别意图并规划研究步骤')
            state['plan'] = await plan_task(query, state['mode'], state['scope'], context, model)
            state['pending'] = state['plan']['actions']
            state['cursor'] = 0
            commit('planned', f"研究目标：{state['plan']['goal']}")
            if state['plan']['intent'] == 'recall':
                commit('intent_recognized', state['plan']['reason'])
        if state.get('workflow_version') == 2 and state['plan']['intent'] == 'research':
            from .full_research import run_full_research
            state['engine'] = 'full_research_v2'
            state['budget']['max_seconds'] = None
            await run_full_research(state, query, uid, sid, model, commit)
            return
        while state['phase'] == 'collecting':
            new_count = state.get('round_new', 0)
            pending = state['pending']
            while state['cursor'] < len(pending) and state['usage']['tool_calls'] < state['budget']['max_tools']:
                action = pending[state['cursor']]
                key = action['tool'] + ':' + action['query'].strip().lower()
                if key in state['seen_actions']:
                    state['cursor'] += 1
                    continue
                state['usage']['tool_calls'] += 1
                trace = {**action, 'number': state['usage']['tool_calls'], 'status': 'running'}
                state['actions'].append(trace)
                commit('tool_started', TOOLS[action['tool']].label + '：' + action['query'])
                tick = time.monotonic()
                try:
                    items = await asyncio.wait_for(execute_tool(action['tool'], action['query'], state['scope'], uid, model), TOOLS[action['tool']].timeout)
                    keys = {evidence_key(e) for e in state['evidence']}
                    for item in items:
                        if evidence_key(item) not in keys:
                            keys.add(evidence_key(item))
                            item['id'] = f"E{len(state['evidence']) + 1}"
                            item['retrieved_at'] = datetime.utcnow().isoformat()
                            state['evidence'].append(item)
                            new_count += 1
                    trace.update(status='completed', results=len(items))
                except Exception as exc:
                    trace.update(status='failed', error=type(exc).__name__)
                    state['warnings'].append(f"{TOOLS[action['tool']].label}失败（{type(exc).__name__}），本次未取得该项结果")
                trace['duration_ms'] = round((time.monotonic() - tick) * 1000)
                state['seen_actions'].append(key)
                state['cursor'] += 1
                state['round_new'] = new_count
                commit('tool_finished', f"{TOOLS[action['tool']].label}：{'已完成' if trace['status'] == 'completed' else '失败，可查看任务提示'}")
            # 评估只调用一次，评估后的状态在继续之前提交。
            if state['plan']['intent'] != 'research' or not state['scope']['sources']:
                state['stop_reason'] = 'direct_answer'
                break
            if not new_count:
                state['stop_reason'] = 'no_new_evidence'
                break
            commit('reviewing', '检查证据覆盖与待补充问题')
            review = await assess_gaps(query, state['plan'], state['evidence'], state['scope'], model)
            state['gaps'] = review['gaps']
            if review['sufficient']:
                state['stop_reason'] = 'evidence_sufficient'
                break
            if state['round'] >= state['budget']['max_rounds'] or state['usage']['tool_calls'] >= state['budget']['max_tools']:
                state['stop_reason'] = 'budget_exhausted'
                break
            unseen = [a for a in review['actions'] if a['tool'] + ':' + a['query'].strip().lower() not in state['seen_actions']]
            if not unseen:
                state['stop_reason'] = 'no_new_query'
                break
            state.update(pending=unseen, cursor=0, round=state['round'] + 1, round_new=0)
            commit('replanned', f"第 {state['round']} 轮：围绕证据缺口补充检索")
        state['phase'] = 'writing'
        commit('writing', '整理结论并核验引用编号')
        # 长期任务可能跨越记忆删除，写作前重新读取，而不复用启动时快照。
        context = await asyncio.to_thread(load_context, sid, uid, state['scope']['use_memory'], query)
        state['context_usage'] = context.get('diagnostics', {})
        state['memory_ids'] = [m['id'] for m in context['memories']]
        report, invalid, cited = await write_report(query, state, context, model)
        state['citation_check'] = {'invalid_ids': invalid, 'cited_ids': cited, 'evidence_count': len(state['evidence']),
                                   'note': '编号检查不代表逐条事实已获语义验证'}
        partial = bool(state['gaps'] or state['warnings'] or invalid or (requires_evidence(state) and state['scope']['sources'] and (not state['evidence'] or not cited)))
        if state['plan']['intent'] == 'research' and state.get('stop_reason') not in ('evidence_sufficient', 'direct_answer'):
            partial = True
        if partial:
            notes = state['gaps'] + state['warnings']
            report += '\n\n### 本次研究的限制\n' + '\n'.join('- ' + s for s in (notes or ['资料覆盖不足，部分结论仍需补充核验。']))
        commit('report_saved', '回答已保存，正在整理会话记忆', 'running', report)
        try:
            state['memory_update'] = await compress_history(sid, model)
        except Exception:
            state['memory_update'] = {'status': 'failed'}
            state['warnings'].append('会话摘要更新失败；原始消息仍保留')
        state['phase'] = 'finished'
        completed_message = '历史回顾完成' if state['plan']['intent'] == 'recall' else '研究完成'
        commit('completed', completed_message if not partial else '已生成结果，仍有资料缺口', 'partial' if partial else 'completed', report)
    try:
        if limit is None:
            await workflow()
        else:
            await asyncio.wait_for(workflow(), max(1, limit - previous_elapsed))
    except asyncio.CancelledError:
        # 取消 API 先提交终态再取消任务，外部同步检索可能延迟返回但不能写入运行结果。
        pass
    except Exception as exc:
        logging.getLogger(__name__).exception('Research run %s failed at %s', run_id, state.get('next_stage', state.get('phase')))
        state['error'] = str(exc) if isinstance(exc, (ModelQuotaExhausted, ModelCallTimedOut, ModelResponseError)) else ('运行超时，已保留完成步骤' if isinstance(exc, asyncio.TimeoutError) else f'步骤执行失败（{type(exc).__name__}），已保留完成步骤')
        commit('failed', state['error'], 'failed')


def new_state(mode, scope, rounds=3, full_research=False):
    return {'mode': mode, 'scope': scope, 'phase': 'collecting', 'round': 1,
            'workflow_version': 2 if full_research else 1,
            'engine': 'full_research_v2' if full_research and mode == 'research' else 'lightweight',
            'budget': {'max_rounds': rounds, 'max_tools': 12, 'max_seconds': None if full_research and mode in ('research', 'auto') else 420},
            'usage': {'model_calls': 0, 'tool_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0},
            'evidence': [], 'actions': [], 'seen_actions': [], 'gaps': [], 'warnings': []}
