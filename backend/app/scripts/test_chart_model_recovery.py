import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.chart_model_calls import cached_call
from service.assistant.llm import MODEL_CONTEXT, ModelResponseTruncated, ModelQuotaExhausted
from service.deep_research_v2.agents.wizard import CodeWizard


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sources = {str(i): {'url': str(i), 'content': 'x' * 5000} for i in range(2)}
        self.deep = {}

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
