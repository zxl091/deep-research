"""检索调度与子步骤恢复：不访问外部模型、搜索、数据库。"""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.agents.scout import DeepScout
from service.assistant.llm import ModelCallTimedOut, ModelResponseError, ModelResponseTruncated, ModelQuotaExhausted


def scout():
    value = object.__new__(DeepScout)
    value.name = 'scout'; value.logger = Mock(); value.add_message = Mock()
    value.fact_fingerprints = {}
    value._execute_search = AsyncMock(return_value=[{'url': 'https://test/source', 'summary': '事实'}])
    return value


def state(count=1):
    return {'phase': 'researching', '_scoped_runtime': True, 'search_web': True, 'search_local': False,
            'query': '测试研究', 'iteration': 0, 'max_iterations': 3, 'facts': [], 'data_points': [],
            'insights': [], 'outline': [{'id': str(i), 'title': f'章节{i}', 'status': 'pending'} for i in range(count)]}


def analysis(name='事实', **extra):
    return {'extracted_facts': [{'content': name, 'source_url': 'https://test/source',
                                'data_points': [{'name': '销量', 'value': 10}]}], **extra}


class ScoutRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_worker_starts_next_chapter_before_slow_one_finishes(self):
        value, data = scout(), state(6)
        release, fourth = asyncio.Event(), asyncio.Event()
        running, peak = 0, 0
        async def work(data, section):
            nonlocal running, peak
            running += 1; peak = max(peak, running)
            try:
                if section['id'] == '0': await release.wait()
                else: await asyncio.sleep(.005)
                if section['id'] == '3': fourth.set()
                section['status'] = 'researched'
            finally: running -= 1
        value._research_section = work
        task = asyncio.create_task(value.process(data))
        try:
            await asyncio.wait_for(fourth.wait(), .5)
            self.assertFalse(task.done())
        finally:
            release.set(); await task
        self.assertLessEqual(peak, 3)
        self.assertTrue(all(s['status'] == 'researched' for s in data['outline']))

    async def test_core_failure_allows_remaining_chapters_and_resume_only_failure(self):
        value, data, visited = scout(), state(6), []
        async def work(data, section):
            visited.append(section['id'])
            if section['id'] == '0' and visited.count('0') == 1: raise ModelCallTimedOut('fixture')
            section['status'] = 'researched'
        value._research_section = work
        with self.assertRaisesRegex(ModelResponseError, '基础分析未完成'): await value.process(data)
        self.assertEqual(len(visited), 6)
        await value.process(data)
        self.assertEqual(visited.count('0'), 2)
        self.assertEqual(len(visited), 7)

    async def test_quota_is_global_and_cancels_other_workers(self):
        value, data, cleaned = scout(), state(6), []
        async def work(data, section):
            try:
                if section['id'] == '0':
                    await asyncio.sleep(.01)
                    raise ModelQuotaExhausted('fixture')
                await asyncio.sleep(60)
            finally: cleaned.append(section['id'])
        value._research_section = work
        with self.assertRaises(ModelQuotaExhausted): await value.process(data)
        self.assertCountEqual(cleaned, ['0', '1', '2'])

    async def test_cancel_and_serialized_resume_do_not_repeat_basic_analysis(self):
        value, data = scout(), state()
        value._analyze_search_results = AsyncMock(return_value=analysis(source_tracing_queries=['追溯']))
        value._execute_deep_search = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError): await value._research_section(data, data['outline'][0])
        saved = json.loads(json.dumps(data))
        resumed = scout()
        resumed._analyze_search_results = AsyncMock(side_effect=AssertionError('must not repeat'))
        resumed._execute_deep_search = AsyncMock()
        await resumed._research_section(saved, saved['outline'][0])
        resumed._execute_search.assert_not_awaited()
        self.assertEqual(len(saved['facts']), 1)
        self.assertEqual(len(saved['data_points']), 1)
        self.assertEqual(saved['outline'][0]['status'], 'researched')

    async def test_nested_trace_retry_preserves_parent_and_clears_only_resolved_gap(self):
        value, data = scout(), state()
        value._search_for_state = AsyncMock(return_value=[{'url': 'https://test/source'}])
        value._analyze_deep_search_results = AsyncMock(side_effect=[
            analysis('父事实', further_tracing_queries=['子查询']), ModelCallTimedOut('fixture'), analysis('子事实')])
        await value._execute_deep_search(data, '0', ['父查询'], 'source_tracing', [])
        self.assertEqual(len(data['facts']), 1)
        self.assertEqual(len(data['research_gaps']), 1)
        # 模拟落盘恢复，父查询不得重新搜索或分析。
        data = json.loads(json.dumps(data))
        await value._execute_deep_search(data, '0', ['父查询'], 'source_tracing', [])
        self.assertEqual(value._analyze_deep_search_results.await_count, 3)
        self.assertEqual(len(data['facts']), 2)
        self.assertFalse(data['research_gaps'])

    async def test_missing_core_evidence_is_not_success(self):
        value, data = scout(), state()
        value._analyze_search_results = AsyncMock(return_value={'extracted_facts': []})
        with self.assertRaises(ModelResponseError): await value._research_section(data, data['outline'][0])
        self.assertEqual(data['outline'][0]['status'], 'researching')
        self.assertNotIn('analysis', data['outline'][0]['research_checkpoint'])

    async def test_truncation_grows_budget_once_without_dropping_evidence(self):
        value = scout()
        value.call_llm = AsyncMock(side_effect=[ModelResponseTruncated('fixture'), '{"extracted_facts":[]}'])
        await value._call_extraction(system_prompt='sys', user_prompt='证据', max_tokens=4000)
        self.assertEqual([c.kwargs['max_tokens'] for c in value.call_llm.call_args_list], [4000, 8000])
        self.assertIn('证据', value.call_llm.call_args.kwargs['user_prompt'])
        value.call_llm = AsyncMock(side_effect=ModelResponseTruncated('fixture'))
        with self.assertRaises(ModelResponseTruncated):
            await value._call_extraction(system_prompt='sys', user_prompt='证据', max_tokens=4000)
        self.assertEqual(value.call_llm.await_count, 2)


if __name__ == '__main__': unittest.main(verbosity=2)
