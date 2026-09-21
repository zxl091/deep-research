import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.chart_model_calls import extraction_request, plan_request
from service.assistant.llm import MODEL_CONTEXT, ModelResponseTruncated, ModelQuotaExhausted
from service.deep_research_v2.agents.wizard import CodeWizard


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sources = {str(i): {'url': str(i), 'content': 'x' * 5000} for i in range(2)}
        self.deep = {}

    async def test_truncation_splits_every_source_and_resume_reuses_cache(self):
        calls = []
        async def request(system, prompt, structured, tokens):
            self.assertEqual(MODEL_CONTEXT.get()['reasoning_effort'], 'low')
            calls.append(prompt)
            if len(calls) == 1: raise ModelResponseTruncated('length')
            source = json.loads(prompt.split('原始摘录：')[1])[0]
            return {'chart': {'type': 'bar', 'points': [{'source_url': source['url'], 'value': 1}]}}
        model = SimpleNamespace(complete=AsyncMock(side_effect=request))
        result = await extraction_request(self.deep, model, 'system', 'prefix', self.sources)
        self.assertEqual(len(result['chart']['points']), 2)
        await extraction_request(json.loads(json.dumps(self.deep)), model, 'system', 'prefix', self.sources)
        self.assertEqual(len(calls), 3)
        self.assertNotIn('reasoning_effort', MODEL_CONTEXT.get())

    async def test_one_unfinished_batch_keeps_other_points_and_explicit_gap(self):
        model = SimpleNamespace(complete=AsyncMock(side_effect=[ModelResponseTruncated('full'),
            {'chart': {'type': 'bar', 'points': [{'value': 1}]}},
            ModelResponseTruncated('batch'), ModelResponseTruncated('batch')]))
        result = await extraction_request(self.deep, model, 's', 'p', self.sources)
        self.assertEqual(len(result['chart']['points']), 1)
        self.assertTrue(result['model_output_incomplete'])
        self.assertIn('截断', result['missing_data'][0])
        self.assertEqual(model.complete.await_count, 4)

    async def test_quota_and_cancellation_propagate_without_retries(self):
        for error in (ModelQuotaExhausted('quota'), asyncio.CancelledError()):
            model = SimpleNamespace(complete=AsyncMock(side_effect=error))
            with self.assertRaises(type(error)):
                await extraction_request({}, model, 's', 'p', self.sources)
            self.assertEqual(model.complete.await_count, 1)

    async def test_changed_evidence_invalidates_cache(self):
        model = SimpleNamespace(complete=AsyncMock(return_value={'chart': None}))
        await extraction_request(self.deep, model, 's', 'p', self.sources)
        self.sources['0']['content'] += 'new evidence'
        await extraction_request(self.deep, model, 's', 'p', self.sources)
        self.assertEqual(model.complete.await_count, 2)

    async def test_bad_points_shape_does_not_crash_batch_merging(self):
        model = SimpleNamespace(complete=AsyncMock(side_effect=[ModelResponseTruncated('full'),
            {'chart': {'points': None}}, {'chart': {'type': 'bar', 'points': [{'value': 1}]}}]))
        result = await extraction_request(self.deep, model, 's', 'p', self.sources)
        self.assertTrue(result['model_output_incomplete'])
        self.assertEqual(result['chart']['points'], [{'value': 1}])

    async def test_plan_retry_is_bounded(self):
        model = SimpleNamespace(complete=AsyncMock(side_effect=ModelResponseTruncated('length')))
        with self.assertRaises(ModelResponseTruncated): await plan_request({}, model, 's', 'p')
        self.assertEqual(model.complete.await_count, 2)

    async def test_saved_sandbox_analysis_is_not_rerun_after_chart_failure(self):
        agent = CodeWizard.__new__(CodeWizard)
        agent.name = 'test'; agent.logger = Mock(); agent.add_message = Mock()
        state = {'query': 'test', 'phase': 'analyzing', 'data_points': [{'value': 1}],
                 'outline': [], 'charts': [], 'code_executions': [], '_scoped_runtime': True}
        async def analyze(state): state['code_executions'].append({'id': 'exec1', 'error': None})
        agent._analyze_data = AsyncMock(side_effect=analyze)
        agent._generate_charts = AsyncMock(side_effect=[ModelResponseTruncated('chart'), None, None])
        with self.assertRaises(ModelResponseTruncated): await agent.process(state)
        await agent.process(json.loads(json.dumps(state)))
        self.assertEqual(agent._analyze_data.await_count, 1)
        state['data_points'].append({'value': 2})
        await agent.process(state)
        self.assertEqual(agent._analyze_data.await_count, 2)


if __name__ == '__main__': unittest.main(verbosity=2)
