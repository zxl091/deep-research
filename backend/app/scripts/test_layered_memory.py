"""隔离账号下验证自动记忆；单测替换模型/向量调用，不改变用户会话。"""
import asyncio
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from uuid import uuid4, uuid5, NAMESPACE_URL
from dotenv import load_dotenv

APP = Path(__file__).resolve().parents[1]
load_dotenv(APP.parent / '.env'); sys.path.insert(0, str(APP))
import models
from core.database import SessionLocal
from models.user import User
from models.chat import ChatSession, ChatMessage, LongTermMemory
from models.assistant import SessionContext
from service.assistant import memory_context as memory


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.uid, self.other, self.sid, self.next_sid = [uuid4() for _ in range(4)]
        with SessionLocal() as db:
            for uid in (self.uid, self.other):
                db.add(User(id=uid, username='qa_memory_' + uid.hex[:12], email=uid.hex + '@example.com', hashed_password='not-a-login'))
            db.flush()
            db.add_all([ChatSession(id=sid, user_id=self.uid, title='memory fixture') for sid in (self.sid, self.next_sid)])
            db.commit()
        self.model = type('Model', (), {})()
        self.model.complete = AsyncMock(return_value={'summary': '研究电动汽车充电，优先核对充电设备利用率。', 'topics': ['充电设备'], 'insights': ['夜间利用率需要调查'], 'unresolved': ['待核实原文']})

    def tearDown(self):
        with SessionLocal() as db:
            db.query(LongTermMemory).filter(LongTermMemory.user_id == self.uid).delete()
            db.query(SessionContext).filter(SessionContext.session_id.in_([self.sid, self.next_sid])).delete()
            db.query(ChatMessage).filter(ChatMessage.session_id.in_([self.sid, self.next_sid])).delete()
            db.query(ChatSession).filter(ChatSession.user_id == self.uid).delete()
            db.query(User).filter(User.id.in_([self.uid, self.other])).delete(); db.commit()
        for sid in (self.sid, self.next_sid):
            key = memory.store.cache_key(self.uid, sid)
            memory.store.cache_client().delete(key, key + ':head')

    def messages(self, count=4):
        with SessionLocal() as db:
            start = datetime.utcnow()
            for i in range(count):
                db.add(ChatMessage(session_id=self.sid, role='user' if i % 2 == 0 else 'assistant', content=f'充电研究消息 {i}', created_at=start + timedelta(microseconds=i)))
            db.commit()

    async def summarize(self):
        self.messages()
        with patch.object(memory.store, 'index_memory'):
            return await memory.compress_history(self.sid, self.model)

    async def test_auto_summary_includes_latest_answer_and_is_idempotent(self):
        result = await self.summarize()
        self.assertEqual(result['index_status'], 'ready')
        self.assertIn('充电研究消息 3', self.model.complete.call_args.args[1])
        with patch.object(memory.store, 'index_memory') as index:
            second = await memory.compress_history(self.sid, self.model)
            self.assertEqual(second['status'], 'not_due'); index.assert_not_called()
        self.assertEqual(self.model.complete.await_count, 1)

    async def test_current_question_is_not_its_own_history(self):
        query = '我之前关于华电科工的研究，主要关注哪些项目和风险'
        with SessionLocal() as db:
            db.add(ChatMessage(session_id=self.sid, role='user', content=query))
            db.commit()
        context = memory.load_context(self.sid, self.uid, False, query)
        self.assertIn(query, context['history'])
        self.assertEqual(context['recall_history'], '')

    async def test_redis_warm_cache_and_expiry_rehydration(self):
        self.messages(6)
        a = memory.load_context(self.sid, self.uid)
        b = memory.load_context(self.sid, self.uid)
        self.assertEqual(a['history'], b['history'])
        self.assertEqual(b['diagnostics']['short_term'], 'redis')
        key = memory.store.cache_key(self.uid, self.sid)
        memory.store.cache_client().delete(key, key + ':head')
        self.assertEqual(memory.load_context(self.sid, self.uid)['history'], a['history'])

    async def test_redis_outage_falls_back_to_pg(self):
        self.messages()
        with patch.object(memory.store, 'read_window', side_effect=RuntimeError), patch.object(memory.store, 'write_window', side_effect=RuntimeError):
            context = memory.load_context(self.sid, self.uid)
        self.assertIn('充电研究消息 3', context['history']); self.assertTrue(context['diagnostics']['warnings'])

    async def test_token_window_keeps_newest_and_obeys_budget(self):
        messages = [{'role':'user', 'content':'早期内容' * 3000}, {'role':'assistant', 'content':'最新回答'}]
        text = memory.window(messages, 30)
        self.assertIn('最新回答', text); self.assertLessEqual(memory.tokens(text), 30)
        self.assertLessEqual(memory.tokens(memory.window(messages[:1], 30)), 30)

    async def test_cross_session_recall_owner_filter_disable_and_delete(self):
        result = await self.summarize()
        with SessionLocal() as db:
            row = db.get(LongTermMemory, result['memory_id']); revision = row.key_insights['revision']
        hit = {'id': result['memory_id'], 'score': 0.9, 'revision': revision}
        with patch.object(memory.store, 'search_memories', return_value=[hit]):
            self.assertEqual(len(memory.load_context(self.next_sid, self.uid, True, '充电')['memories']), 1)
            self.assertFalse(memory.load_context(self.next_sid, self.other, True, '充电')['memories'])
            self.assertFalse(memory.load_context(self.next_sid, self.uid, False, '充电')['memories'])
            self.assertFalse(memory.load_context(self.sid, self.uid, True, '充电')['memories'])
            with SessionLocal() as db:
                db.query(LongTermMemory).filter(LongTermMemory.id == result['memory_id']).delete(); db.commit()
            self.assertFalse(memory.load_context(self.next_sid, self.uid, True, '充电')['memories'])

    async def test_index_outage_does_not_lose_summary_and_can_retry(self):
        self.messages()
        with patch.object(memory.store, 'index_memory', side_effect=RuntimeError):
            result = await memory.compress_history(self.sid, self.model)
        self.assertEqual(result['index_status'], 'pending')
        with patch.object(memory.store, 'index_memory'):
            self.assertEqual(memory.retry_index(result['memory_id']), 'ready')
        self.assertEqual(self.model.complete.await_count, 1)

    async def test_bad_summary_does_not_advance_cursor(self):
        self.messages(); self.model.complete.return_value = {'summary': ''}
        with self.assertRaises(ValueError):
            await memory.compress_history(self.sid, self.model)
        with SessionLocal() as db:
            self.assertIsNone(db.get(SessionContext, self.sid))

    async def test_incremental_summary_only_adds_new_messages(self):
        await self.summarize()
        with SessionLocal() as db:
            db.add(ChatMessage(session_id=self.sid, role='user', content='新增独立问题')); db.commit()
        with patch.object(memory.store, 'index_memory'):
            await memory.compress_history(self.sid, self.model, force=True)
        payload = self.model.complete.call_args.args[1]
        self.assertIn('新增独立问题', payload); self.assertNotIn('充电研究消息 0', payload)


if __name__ == '__main__': unittest.main(verbosity=2)
