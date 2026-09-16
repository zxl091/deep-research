"""历史回顾与新研究的路由回归；不调用外部模型或搜索。"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from dotenv import load_dotenv

APP = Path(__file__).resolve().parents[1]
load_dotenv(APP.parent / '.env')
sys.path.insert(0, str(APP))
from service.assistant.controller import plan_task, write_report, explicit_history_recall

QUERY = '我之前关于华电科工的研究，主要关注哪些项目和风险'
SCOPE = {'sources': ['web'], 'kb_ids': [], 'use_memory': True}


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_reproduction_cannot_escalate_to_research(self):
        model = SimpleNamespace(complete=AsyncMock())
        for mode in ('auto', 'answer'):
            plan = await plan_task(QUERY, mode, SCOPE, {}, model)
            self.assertEqual(plan['intent'], 'recall')
            self.assertEqual(plan['goal'], QUERY)
            self.assertEqual(plan['actions'], [])
        model.complete.assert_not_awaited()

    async def test_semantic_recall_also_suppresses_model_tool_suggestions(self):
        model = SimpleNamespace(complete=AsyncMock(return_value={
            'intent': 'recall', 'actions': [{'tool': 'web_search', 'query': 'unwanted'}]}))
        plan = await plan_task('那时候得出的结论是啥？', 'auto', SCOPE, {}, model)
        self.assertEqual(plan['intent'], 'recall')
        self.assertEqual(plan['actions'], [])

    async def test_explicit_research_mode_is_not_overridden(self):
        model = SimpleNamespace(complete=AsyncMock(return_value={'intent': 'recall'}))
        plan = await plan_task(QUERY, 'research', SCOPE, {}, model)
        self.assertEqual(plan['intent'], 'research')
        self.assertTrue(plan['actions'])

    async def test_new_research_requests_go_to_planner(self):
        model = SimpleNamespace(complete=AsyncMock(return_value={'intent': 'research'}))
        for query in [QUERY + '，并核实最新进展', QUERY + '，重新研究一下',
                      QUERY + '，请写一篇深度研究报告', '研究华电科工的项目和风险',
                      '我之前提到过风险，现在是否还成立？']:
            with self.subTest(query=query):
                self.assertFalse(explicit_history_recall(query))
                self.assertEqual((await plan_task(query, 'auto', SCOPE, {}, model))['intent'], 'research')
        self.assertEqual(model.complete.await_count, 5)

    async def test_no_forced_search_for_simple_answer_or_invalid_intent(self):
        for intent in ('answer', 'unexpected'):
            model = SimpleNamespace(complete=AsyncMock(return_value={'intent': intent, 'actions': []}))
            plan = await plan_task('你好', 'auto', SCOPE, {}, model)
            self.assertEqual(plan['intent'], 'answer')
            self.assertEqual(plan['actions'], [])

    async def test_recall_can_use_memory_without_web_evidence(self):
        model = SimpleNamespace(complete=AsyncMock(return_value='根据之前的摘要，讨论了合同履约风险。'))
        state = {'plan': {'intent': 'recall'}, 'scope': SCOPE, 'evidence': []}
        context = {'history': 'user: ' + QUERY, 'recall_history': '', 'summary': '',
                   'memories': [{'content': '之前讨论了合同履约风险', 'session_id': 'source'}]}
        report, invalid, cited = await write_report(QUERY, state, context, model)
        self.assertIn('合同履约', report)
        self.assertEqual(invalid, [])
        self.assertEqual(cited, [])
        payload = json.loads(model.complete.call_args.args[1])
        self.assertEqual(payload['history']['recent_dialogue'], '')
        self.assertEqual(payload['history']['recalled_summaries'], context['memories'])

    async def test_missing_history_does_not_invent_memories(self):
        model = SimpleNamespace(complete=AsyncMock())
        state = {'plan': {'intent': 'recall'}, 'scope': SCOPE, 'evidence': []}
        report, _, _ = await write_report(QUERY, state, {'history': 'user: ' + QUERY}, model)
        self.assertIn('没有找到', report)
        model.complete.assert_not_awaited()

    async def test_research_without_evidence_still_refuses_factual_report(self):
        model = SimpleNamespace(complete=AsyncMock())
        state = {'plan': {'intent': 'research'}, 'scope': SCOPE, 'evidence': []}
        report, _, _ = await write_report(QUERY, state, {'summary': '旧结论'}, model)
        self.assertIn('没有获得可用证据', report)
        model.complete.assert_not_awaited()


if __name__ == '__main__':
    unittest.main(verbosity=2)
