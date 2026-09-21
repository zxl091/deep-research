"""Offline regression: all-fact coverage, truncation recovery and persistent resume."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.analysis_batches import extract_in_batches, fact_batches, validate_result
from service.assistant.llm import ModelResponseTruncated, ModelQuotaExhausted, ModelResponseError
from service.deep_research_v2.agents.data_analyst import DataAnalyst


def facts(n):
    return [{'id': str(i), 'content': f'企业{i}收入{i + 1}亿元', 'source_url': f'https://test/{i}'} for i in range(n)]


def items(prompt):
    return json.loads(prompt.split('本批事实：\n')[1].split('\n仅返回 JSON')[0])


def result(batch):
    return {'data_points': [{'name': f['content'], 'value': 1, 'unit': '亿元',
                            'source_fact_id': f['fact_id']} for f in batch],
            'insights': [], 'time_series': [], 'distributions': []}


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, call):
        return SimpleNamespace(call_llm=AsyncMock(side_effect=call), add_message=lambda *a: None)

    async def test_all_facts_and_resume_without_duplicate_calls(self):
        async def call(**kw): return json.dumps(result(items(kw['user_prompt'])))
        agent = self.agent(call)
        state = {'query': 'test', 'facts': facts(31)}
        out = await extract_in_batches(agent, state)
        self.assertEqual(len(out['data_points']), 31)
        self.assertEqual(agent.call_llm.await_count, 3)
        restored = json.loads(json.dumps(state))
        await extract_in_batches(agent, restored)
        self.assertEqual(agent.call_llm.await_count, 3)
        self.assertEqual(len(restored['data_points']), 31)
        restored['facts'].append(facts(32)[-1])
        await extract_in_batches(agent, restored)
        self.assertEqual(len(restored['data_points']), 32)

    async def test_split_then_quota_resume_skips_saved_children(self):
        seen = []
        blocked = True
        async def call(**kw):
            batch = items(kw['user_prompt']); seen.append([x['content'] for x in batch])
            if len(batch) > 2: raise ModelResponseTruncated('length')
            if blocked and '企业2' in batch[0]['content']: raise ModelQuotaExhausted('quota')
            return json.dumps(result(batch))
        agent = self.agent(call); state = {'query': 'test', 'facts': facts(4)}
        with self.assertRaises(ModelQuotaExhausted): await extract_in_batches(agent, state)
        self.assertEqual(len(state['data_points']), 2)
        before = len(seen); blocked = False
        await extract_in_batches(agent, json.loads(json.dumps(state)))
        self.assertEqual(len(seen) - before, 1)
        self.assertEqual(len(seen[-1]), 2)

    async def test_singleton_has_bounded_retry_no_partial_json_accepted(self):
        async def call(**kw): raise ModelResponseTruncated('length')
        agent = self.agent(call); state = {'query': 'test', 'facts': facts(1)}
        with self.assertRaisesRegex(ModelResponseError, '最小批次'): await extract_in_batches(agent, state)
        self.assertEqual(agent.call_llm.await_count, 2)
        self.assertFalse(state.get('data_points'))
        self.assertFalse(state['analysis_checkpoints']['extraction_v1'])

    async def test_truncated_singleton_can_recover_with_complete_result(self):
        seen = []
        async def call(**kw):
            seen.append(kw['max_tokens'])
            if len(seen) == 1: raise ModelResponseTruncated('length')
            return json.dumps(result(items(kw['user_prompt'])))
        state = {'query': 'test', 'facts': facts(1)}
        await extract_in_batches(self.agent(call), state)
        self.assertEqual(seen, [6000, 12000])
        self.assertEqual(len(state['data_points']), 1)

    async def test_cancel_and_provider_errors_are_not_retried(self):
        for error in (asyncio.CancelledError(), RuntimeError('provider rejection')):
            async def call(**kw): raise error
            agent = self.agent(call)
            with self.assertRaises(type(error)): await extract_in_batches(agent, {'query': 'test', 'facts': facts(1)})
            self.assertEqual(agent.call_llm.await_count, 1)

    def test_oversized_fact_tail_and_source_are_preserved(self):
        batch = list(fact_batches([{'content': 'a' * 15000 + 'TAIL', 'source_url': 'https://test'}]))
        flat = [f for b in batch for f in b]
        self.assertEqual(''.join(f['content'] for f in flat), 'a' * 15000 + 'TAIL')
        self.assertTrue(all(f['source_url'] == 'https://test' for f in flat))

    def test_invalid_shape_and_foreign_sources_rejected(self):
        batch = next(fact_batches(facts(1)))
        for value in ({}, {'data_points': 'wrong'}, []):
            with self.assertRaises(ModelResponseError): validate_result(value, batch)
        value = result(batch); value['data_points'][0]['source_fact_id'] = 'other'
        with self.assertRaises(ModelResponseError): validate_result(value, batch)

    async def test_empty_input_makes_no_call(self):
        agent = self.agent(None)
        await extract_in_batches(agent, {'query': 'test', 'facts': []})
        agent.call_llm.assert_not_called()

    async def test_graph_truncation_retry_and_checkpoint(self):
        agent = DataAnalyst.__new__(DataAnalyst)
        agent.logger = SimpleNamespace(info=lambda *a: None, debug=lambda *a: None)
        graph = {'nodes': [{'id': 'one', 'name': '企业', 'importance': 8}], 'edges': []}
        agent.call_llm = AsyncMock(side_effect=[ModelResponseTruncated('length'), json.dumps(graph)])
        state = {'query': 'test', 'facts': facts(1)}
        first = await agent._build_knowledge_graph(state)
        second = await agent._build_knowledge_graph(json.loads(json.dumps(state)))
        self.assertEqual(first, second)
        self.assertEqual(agent.call_llm.await_count, 2)
        self.assertEqual(first['nodes'][0]['size'], 44)


if __name__ == '__main__': unittest.main(verbosity=2)
