"""使用隔离 PostgreSQL 记录验证运行时；模型与工具替身用于确定性故障测试。"""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from dotenv import load_dotenv

APP = Path(__file__).resolve().parents[1]
load_dotenv(APP.parent / '.env')
sys.path.insert(0, str(APP))
from core.database import Base, engine, SessionLocal
import models
from models.user import User
from models.chat import ChatSession, ChatMessage, LongTermMemory
from models.assistant import AssistantRun, SessionContext
from service.assistant import runtime
from service.assistant.controller import ground_report, normalize_actions
from service.assistant.context import load_context
from service.assistant.sql_policy import validate_sql, execute_readonly, BUSINESS_TABLES
from service.database_explorer import DatabaseExplorer


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Base.metadata.create_all(engine)
        self.uid, self.sid = uuid4(), uuid4()
        with SessionLocal() as db:
            db.add(User(id=self.uid, username='qa_runtime_' + self.uid.hex[:10], email=self.uid.hex + '@example.com', hashed_password='not-a-login'))
            db.flush()
            db.add(ChatSession(id=self.sid, user_id=self.uid, title='runtime fixture'))
            db.commit()

    def tearDown(self):
        with SessionLocal() as db:
            db.query(AssistantRun).filter(AssistantRun.user_id == self.uid).delete()
            db.query(LongTermMemory).filter(LongTermMemory.user_id == self.uid).delete()
            db.query(ChatMessage).filter(ChatMessage.session_id == self.sid).delete()
            db.query(SessionContext).filter(SessionContext.session_id == self.sid).delete()
            db.query(ChatSession).filter(ChatSession.id == self.sid).delete()
            db.query(User).filter(User.id == self.uid).delete()
            db.commit()

    def make_run(self, state=None):
        rid = uuid4()
        with SessionLocal() as db:
            db.add(AssistantRun(id=rid, user_id=self.uid, session_id=self.sid, query='验证研究', state=state or runtime.new_state('research', {'sources': ['local'], 'kb_ids': [], 'use_memory': False}), events=[], status='queued'))
            db.commit()
        return str(rid)

    def get(self, rid):
        with SessionLocal() as db:
            return runtime.snapshot(db.get(AssistantRun, rid))

    async def run_fake(self, rid, tool=None, review=None):
        plan = {'intent': 'research', 'goal': '验证', 'questions': ['Q'], 'actions': [{'tool': 'knowledge_search', 'query': 'Q'}]}
        item = {'title': 'fixture', 'url': 'https://example.com/fixture', 'content': 'FACT-4726', 'source': 'knowledge'}
        with patch.object(runtime, 'plan_task', AsyncMock(return_value=plan)), \
             patch.object(runtime, 'execute_tool', tool or AsyncMock(return_value=[item])), \
             patch.object(runtime, 'assess_gaps', AsyncMock(return_value=review or {'sufficient': True, 'gaps': [], 'actions': []})), \
             patch.object(runtime, 'write_report', AsyncMock(return_value=('FACT-4726 [E1]', [], ['E1']))), \
             patch.object(runtime, 'compress_history', AsyncMock(return_value={'status': 'not_due'})):
            await runtime.execute(rid)

    async def test_completion_saved_once(self):
        rid = self.make_run()
        await self.run_fake(rid)
        await self.run_fake(rid)
        self.assertEqual(self.get(rid)['status'], 'completed')
        with SessionLocal() as db:
            self.assertEqual(db.query(ChatMessage).filter(ChatMessage.session_id == self.sid, ChatMessage.role == 'assistant').count(), 1)

    async def test_auto_recall_completes_without_tools_or_full_research(self):
        from service.assistant import full_research
        query = '我之前关于华电科工的研究，主要关注哪些项目和风险'
        state = runtime.new_state('auto', {'sources': ['web'], 'kb_ids': [], 'use_memory': True}, full_research=True)
        rid = self.make_run(state)
        with SessionLocal() as db:
            db.get(AssistantRun, rid).query = query
            db.commit()
        context = {'recall_history': '', 'summary': '', 'memories': [{'id': 'fixture', 'content': '历史合同风险'}]}
        model = type('Model', (), {'complete': AsyncMock(return_value='根据历史摘要，讨论了合同履约风险。')})()
        with patch.object(runtime, 'load_context', return_value=context), \
             patch.object(runtime, 'ModelGateway', return_value=model), \
             patch.object(runtime, 'execute_tool', AsyncMock()) as tool, \
             patch.object(full_research, 'run_full_research', AsyncMock()) as full, \
             patch.object(runtime, 'compress_history', AsyncMock(return_value={'status': 'not_due'})):
            await runtime.execute(rid)
        saved = self.get(rid)
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['state']['plan']['intent'], 'recall')
        self.assertEqual(saved['state']['engine'], 'lightweight')
        self.assertEqual(saved['state']['usage']['tool_calls'], 0)
        self.assertNotIn('资料覆盖不足', saved['report'])
        tool.assert_not_awaited(); full.assert_not_awaited()
        model.complete.assert_awaited_once()

    async def test_expired_full_run_resumes_without_resetting_usage_or_evidence(self):
        from uuid import UUID
        from router import assistant_router
        from service.assistant import full_research
        state = runtime.new_state('research', {'sources':['web'], 'kb_ids':[], 'use_memory':False}, full_research=True)
        state['budget']['max_seconds'] = 1800
        state['usage']['elapsed_seconds'] = 1800
        state['next_stage'] = 'analyze'
        state['source_snapshots'] = {'fixture': {'content':'保留证据'}}
        state['error'] = '运行超时，已保留完成步骤'
        rid = self.make_run(state)
        with SessionLocal() as db, patch.object(assistant_router, 'launch') as launch:
            run = db.get(AssistantRun, UUID(rid)); run.status = 'failed'; db.commit()
            result = await assistant_router.resume(UUID(rid), db.get(User, self.uid), db)
            launch.assert_called_once_with(rid)
        self.assertEqual(result['status'], 'queued')
        self.assertIsNone(result['state']['budget']['max_seconds'])
        self.assertEqual(result['state']['usage']['elapsed_seconds'], 1800)
        self.assertEqual(result['state']['next_stage'], 'analyze')
        async def continued(state, query, uid, sid, model, commit):
            self.assertEqual(state['source_snapshots']['fixture']['content'], '保留证据')
            await asyncio.sleep(1.1)  # 旧逻辑在时间预算耗尽后只剩 1 秒，会在这里失败。
            commit('completed', 'fixture completed', 'completed', 'fixture report')
        with patch.object(full_research, 'run_full_research', continued):
            await runtime.execute(rid)
        saved = self.get(rid)
        self.assertEqual(saved['status'], 'completed')
        self.assertGreater(saved['state']['usage']['elapsed_seconds'], 1801)

    async def test_unlimited_full_run_can_still_be_cancelled(self):
        from service.assistant import full_research
        state = runtime.new_state('research', {'sources':['web'], 'kb_ids':[], 'use_memory':False}, full_research=True)
        rid = self.make_run(state); entered = asyncio.Event(); stopped = asyncio.Event()
        async def ongoing(*args):
            entered.set()
            try: await asyncio.sleep(60)
            finally: stopped.set()
        with patch.object(full_research, 'run_full_research', ongoing):
            task = asyncio.create_task(runtime.execute(rid))
            await asyncio.wait_for(entered.wait(), 3)
            with SessionLocal() as db:
                run = db.get(AssistantRun, rid); run.status = 'cancelled'; db.commit()
            task.cancel(); await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.get(rid)['status'], 'cancelled')
        self.assertFalse(self.get(rid)['report'])

    async def test_quota_failure_is_clear_and_keeps_checkpoint(self):
        state = runtime.new_state('research', {'sources':['web'], 'kb_ids':[], 'use_memory':False}, full_research=True)
        state['draft_sections'] = {'s1':'已保存章节'}
        rid = self.make_run(state)
        from service.assistant import full_research
        with patch.object(full_research, 'run_full_research', AsyncMock(side_effect=runtime.ModelQuotaExhausted('当前模型免费额度已耗尽'))):
            await runtime.execute(rid)
        saved=self.get(rid)
        self.assertEqual(saved['status'],'failed')
        self.assertIn('额度已耗尽',saved['state']['error'])
        self.assertEqual(saved['state']['draft_sections'],{'s1':'已保存章节'})

    async def test_model_call_timeout_keeps_full_research_resumable(self):
        state = runtime.new_state('research', {'sources':['web'], 'kb_ids':[], 'use_memory':False}, full_research=True)
        state.update(next_stage='write', draft_sections={'s1':'已有章节'})
        rid = self.make_run(state)
        from service.assistant import full_research
        with patch.object(full_research, 'run_full_research', AsyncMock(side_effect=runtime.ModelCallTimedOut('单次模型请求超过 600 秒'))):
            await runtime.execute(rid)
        saved = self.get(rid)
        self.assertEqual(saved['status'], 'failed')
        self.assertIn('单次模型请求', saved['state']['error'])
        self.assertEqual(saved['state']['next_stage'], 'write')
        self.assertEqual(saved['state']['draft_sections'], {'s1':'已有章节'})
        self.assertIsNone(saved['state']['budget']['max_seconds'])

    async def test_failure_is_partial_not_false_success(self):
        rid = self.make_run()
        await self.run_fake(rid, AsyncMock(side_effect=TimeoutError()))
        result = self.get(rid)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['state']['actions'][0]['status'], 'failed')

    async def test_repeated_query_stops_loop(self):
        rid = self.make_run()
        tool = AsyncMock(return_value=[{'title': 'fixture', 'url': 'https://example.com', 'content': 'F', 'source': 'knowledge'}])
        await self.run_fake(rid, tool, {'sufficient': False, 'gaps': ['缺少证据'], 'actions': [{'tool': 'knowledge_search', 'query': 'Q'}]})
        self.assertEqual(tool.await_count, 1)
        self.assertEqual(self.get(rid)['state']['stop_reason'], 'no_new_query')

    async def test_resume_skips_committed_tool(self):
        state = runtime.new_state('research', {'sources': ['local'], 'kb_ids': [], 'use_memory': False})
        state.update(plan={'goal': 'resume', 'intent': 'research', 'questions': ['Q']}, pending=[{'tool': 'knowledge_search', 'query': 'Q'}], cursor=1, round_new=1,
                     evidence=[{'id': 'E1', 'title': 'fixture', 'url': 'https://example.com', 'content': 'FACT-4726', 'source': 'knowledge'}])
        tool = AsyncMock()
        rid = self.make_run(state)
        await self.run_fake(rid, tool)
        self.assertEqual(tool.await_count, 0)
        self.assertEqual(self.get(rid)['status'], 'completed')

    async def test_cancelled_run_cannot_be_written(self):
        rid = self.make_run()
        with SessionLocal() as db:
            run = db.get(AssistantRun, rid); run.status = 'cancelled'; db.commit()
        with self.assertRaises(asyncio.CancelledError):
            runtime.save(rid, {}, 'late', 'late')
        self.assertEqual(self.get(rid)['status'], 'cancelled')

    async def test_manual_preferences_are_excluded_from_auto_memory(self):
        mid = uuid4()
        with SessionLocal() as db:
            db.add(LongTermMemory(id=mid, user_id=self.uid, summary='先给结论', key_insights={'explicit': True}))
            db.commit()
        self.assertFalse(load_context(self.sid, self.uid)['memories'])
        self.assertFalse(load_context(self.sid, uuid4())['memories'])
        with SessionLocal() as db:
            db.query(LongTermMemory).filter(LongTermMemory.id == mid).delete(); db.commit()
        self.assertFalse(load_context(self.sid, self.uid)['memories'])

    async def test_sql_rejects_private_tables_and_functions(self):
        with SessionLocal() as db:
            for sql in ['SELECT * FROM users WHERE false', 'SELECT * FROM users', 'SELECT (SELECT count(*) FROM chat_messages)', 'SELECT pg_read_file(\'/etc/passwd\')', 'SELECT 1; DELETE FROM users', 'SELECT * FROM company_data, users']:
                with self.subTest(sql=sql), self.assertRaises(ValueError):
                    execute_readonly(db, sql)
            self.assertEqual(execute_readonly(db, 'SELECT 7 AS value')['rows'][0]['value'], 7)
            self.assertEqual(str(execute_readonly(db, "SELECT EXTRACT(YEAR FROM DATE '2024-01-01') AS year")['rows'][0]['year']), '2024')
            self.assertLessEqual(execute_readonly(db, 'SELECT * FROM company_data LIMIT 999999')['row_count'], 100)

    async def test_database_explorer_only_business_tables(self):
        with SessionLocal() as db:
            explorer = DatabaseExplorer(db)
            self.assertTrue({t['name'] for t in explorer.get_tables()} <= BUSINESS_TABLES)
            with self.assertRaises(ValueError): explorer.get_table_data('users')
            self.assertTrue(explorer.get_table_schema('company_data')['columns'])

    async def test_citation_and_tool_contract(self):
        report, invalid, cited = ground_report('事实 [E1] 编造 [E99] [外链](https://fake.invalid)', [{'id': 'E1'}])
        self.assertNotIn('fake.invalid', report)
        self.assertEqual(invalid, ['E99']); self.assertEqual(cited, ['E1'])
        self.assertEqual(normalize_actions([{'tool': 'web_search', 'query': 'Q'}], ['knowledge_search']), [])

    async def test_tool_budget_cannot_be_exceeded(self):
        state = runtime.new_state('research', {'sources': ['local'], 'kb_ids': [], 'use_memory': False})
        state['budget']['max_tools'] = 1
        rid = self.make_run(state)
        await self.run_fake(rid, review={'sufficient': False, 'gaps': ['缺口'], 'actions': [{'tool': 'knowledge_search', 'query': 'different'}]})
        result = self.get(rid)
        self.assertEqual(result['state']['usage']['tool_calls'], 1)
        self.assertEqual(result['state']['stop_reason'], 'budget_exhausted')
        self.assertEqual(result['status'], 'partial')

    async def test_context_history_is_user_scoped(self):
        with SessionLocal() as db:
            db.add(ChatMessage(session_id=self.sid, role='user', content='PRIVATE-HISTORY'))
            db.commit()
        self.assertIn('PRIVATE-HISTORY', load_context(self.sid, self.uid)['history'])
        self.assertNotIn('PRIVATE-HISTORY', load_context(self.sid, uuid4())['history'])

    async def test_cancel_inflight_does_not_save_result(self):
        entered = asyncio.Event()
        async def slow_tool(*args):
            entered.set()
            await asyncio.sleep(60)
        rid = self.make_run()
        task = asyncio.create_task(self.run_fake(rid, slow_tool))
        await asyncio.wait_for(entered.wait(), 3)
        with SessionLocal() as db:
            run = db.get(AssistantRun, rid); run.status = 'cancelled'; db.commit()
        task.cancel()
        await task
        result = self.get(rid)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['report'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
