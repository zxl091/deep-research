import asyncio
import copy
import json
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from core.database import get_db, SessionLocal
from models.chat import ChatSession, ChatMessage, LongTermMemory
from models.assistant import AssistantRun
from router.auth_router import get_current_user_required
from service.knowledge_index import resolve_knowledge_ids
from service.assistant.runtime import ACTIVE, tasks, launch, snapshot, new_state, run_time_limit

router = APIRouter(prefix='/assistant', tags=['个人研究助手'])


class RunRequest(BaseModel):
    session_id: UUID
    query: str = Field(min_length=1, max_length=8000)
    mode: Literal['auto', 'answer', 'research', 'sql'] = 'research'
    sources: list[Literal['web', 'local', 'database']] = Field(default_factory=lambda: ['web'])
    kb_ids: list[UUID] = Field(default_factory=list)
    use_memory: bool = True
    max_rounds: int = Field(default=3, ge=1, le=3)


def owned_run(db, rid, uid):
    run = db.query(AssistantRun).filter(AssistantRun.id == rid, AssistantRun.user_id == uid).first()
    if not run:
        raise HTTPException(404, '研究任务不存在')
    return run


@router.post('/runs', status_code=201)
async def create_run(body: RunRequest, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == body.session_id, ChatSession.user_id == user.id).with_for_update().first()
    if not session:
        raise HTTPException(404, '会话不存在')
    if not body.query.strip():
        raise HTTPException(400, '请输入研究问题')
    if db.query(AssistantRun).filter(AssistantRun.session_id == session.id, AssistantRun.status.in_(ACTIVE)).first():
        raise HTTPException(409, '本会话已有任务运行中')
    if body.mode == 'sql' and 'database' not in body.sources:
        raise HTTPException(400, '数据库查询模式需要启用业务数据库')
    if 'local' in body.sources and not body.kb_ids:
        raise HTTPException(400, '请至少选择一个知识库')
    kb_ids = resolve_knowledge_ids(db, user.id, body.kb_ids) if 'local' in body.sources else []
    state = new_state(body.mode, {'sources': list(dict.fromkeys(body.sources)), 'kb_ids': kb_ids, 'use_memory': body.use_memory}, body.max_rounds, full_research=True)
    run = AssistantRun(session_id=session.id, user_id=user.id, query=body.query.strip(), status='queued', state=state, events=[])
    db.add(run)
    db.add(ChatMessage(session_id=session.id, role='user', content=body.query.strip()))
    from datetime import datetime
    session.updated_at = datetime.utcnow()
    if session.title in ('新对话', '新的研究'):
        session.title = body.query.strip()[:40]
    db.commit()
    db.refresh(run)
    launch(str(run.id))
    return snapshot(run)


@router.get('/runs')
def list_runs(session_id: UUID, offset: int = Query(0, ge=0), user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not session:
        raise HTTPException(404, '会话不存在')
    return [snapshot(r) for r in db.query(AssistantRun).filter(AssistantRun.session_id == session_id, AssistantRun.user_id == user.id).order_by(AssistantRun.created_at.desc(), AssistantRun.id.desc()).offset(offset).limit(30)]


@router.get('/runs/{run_id}')
def get_run(run_id: UUID, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    return snapshot(owned_run(db, run_id, user.id))


@router.get('/runs/{run_id}/events')
async def events(run_id: UUID, after: int = Query(0, ge=0), user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    owned_run(db, run_id, user.id)
    uid = user.id
    async def stream():
        cursor = after
        while True:
            with SessionLocal() as connection:
                run = connection.query(AssistantRun).filter(AssistantRun.id == run_id, AssistantRun.user_id == uid).first()
                if not run:
                    return
                data = snapshot(run)
            for event in data['events']:
                if event['seq'] > cursor:
                    cursor = event['seq']
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if data['status'] not in ACTIVE:
                yield 'event: done\ndata: {}\n\n'
                return
            yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/runs/{run_id}/cancel')
async def cancel(run_id: UUID, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    run = owned_run(db, run_id, user.id)
    if run.status in ACTIVE | {'interrupted'}:
        run.status = 'cancelled'
        db.commit()
        task = tasks.get(str(run_id))
        if task:
            task.cancel()
    return snapshot(run)


@router.post('/runs/{run_id}/resume')
async def resume(run_id: UUID, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    run = owned_run(db, run_id, user.id)
    db.query(ChatSession).filter(ChatSession.id == run.session_id).with_for_update().first()
    if run.status not in ('interrupted', 'failed'):
        raise HTTPException(409, '只有中断或失败的任务可以恢复')
    if db.query(AssistantRun).filter(AssistantRun.session_id == run.session_id, AssistantRun.status.in_(ACTIVE)).first():
        raise HTTPException(409, '本会话已有其他任务运行中')
    state = copy.deepcopy(run.state)
    resolve_knowledge_ids(db, user.id, state['scope']['kb_ids'])
    limit = run_time_limit(state)
    state['budget']['max_seconds'] = limit
    if limit is not None and state['usage'].get('elapsed_seconds', 0) >= limit:
        raise HTTPException(409, '本次运行已达到时间预算，请发起新任务')
    for action in state['actions']:
        if action['status'] == 'running':
            action['status'] = 'interrupted'
    state.pop('error', None)
    run.state, run.status = state, 'queued'
    db.commit()
    launch(str(run.id))
    return snapshot(run)


class MemoryBody(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


def memory_data(m):
    return {'id': str(m.id), 'content': m.summary, 'explicit': bool((m.key_insights or {}).get('explicit')),
            'created_at': m.created_at.isoformat(), 'session_id': str(m.session_id) if m.session_id else None,
            'metadata': m.key_insights or {}, 'token_count': m.token_count}


@router.get('/memories')
def memories(user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    return [memory_data(m) for m in db.query(LongTermMemory).join(ChatSession, LongTermMemory.session_id == ChatSession.id).filter(
        LongTermMemory.user_id == user.id, ChatSession.user_id == user.id,
        LongTermMemory.key_insights['kind'].astext == 'auto_summary_v1').order_by(LongTermMemory.created_at.desc()).limit(100)]


@router.post('/sessions/{session_id}/memory')
async def summarize_session(session_id: UUID, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    if not db.query(ChatSession.id).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first():
        raise HTTPException(404, '会话不存在')
    if db.query(AssistantRun.id).filter(AssistantRun.session_id == session_id, AssistantRun.status.in_(ACTIVE)).first():
        raise HTTPException(409, '请等待当前任务完成后整理记忆')
    from service.assistant.context import compress_history
    from service.assistant.llm import ModelGateway
    try:
        return await compress_history(session_id, ModelGateway(), force=True)
    except Exception:
        raise HTTPException(503, '记忆整理失败，原始消息已保留，可稍后重试')


@router.post('/memories', status_code=201)
def add_memory(body: MemoryBody, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    if not body.content.strip():
        raise HTTPException(400, '记忆内容不能为空')
    memory = LongTermMemory(user_id=user.id, summary=body.content.strip(), key_insights={'explicit': True})
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory_data(memory)


@router.put('/memories/{memory_id}')
def edit_memory(memory_id: UUID, body: MemoryBody, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    memory = db.query(LongTermMemory).filter(LongTermMemory.id == memory_id, LongTermMemory.user_id == user.id).first()
    if not memory:
        raise HTTPException(404, '记忆不存在')
    if not body.content.strip():
        raise HTTPException(400, '记忆内容不能为空')
    memory.summary, memory.key_insights = body.content.strip(), {'explicit': True}
    memory.milvus_ids = []  # 旧自动摘要向量不能覆盖用户修改后的内容。
    db.commit()
    return memory_data(memory)


@router.delete('/memories/{memory_id}', status_code=204)
def delete_memory(memory_id: UUID, user=Depends(get_current_user_required), db: Session = Depends(get_db)):
    memory = db.query(LongTermMemory).filter(LongTermMemory.id == memory_id, LongTermMemory.user_id == user.id).first()
    if not memory:
        raise HTTPException(404, '记忆不存在')
    # 先删除权威记录；即使向量服务离线，召回时的 PG 核验也会拦截残留向量。
    db.delete(memory)
    db.commit()
    try:
        from service.assistant.memory_store import delete_index
        delete_index(memory_id)
    except Exception:
        pass
