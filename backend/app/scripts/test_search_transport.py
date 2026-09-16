"""搜索连接错误不能伪装成成功的零结果，不访问外网。"""
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.agents import scout


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = object.__new__(scout.DeepScout)
        self.agent.search_cache = {}
        self.agent.search_api_key = 'fixture'
        self.agent.logger = logging.getLogger('qa.search')

    async def invoke(self, response=None, error=None):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post = AsyncMock(return_value=response, side_effect=error)
        with patch.object(scout.httpx, 'AsyncClient', return_value=client) as factory:
            try:
                return await self.agent._execute_search('RAG')
            finally:
                self.assertFalse(factory.call_args.kwargs['trust_env'])

    async def test_connection_failure_propagates_without_cache(self):
        with self.assertRaises(httpx.ConnectError):
            await self.invoke(error=httpx.ConnectError('fixture TLS error'))
        self.assertFalse(self.agent.search_cache)

    async def test_http_and_provider_failure_propagate(self):
        for status, code in ((503, 503), (200, 403)):
            response = httpx.Response(status, json={'code': code}, request=httpx.Request('POST', 'https://example.test'))
            with self.subTest(status=status), self.assertRaises((httpx.HTTPStatusError, RuntimeError)):
                await self.invoke(response)
        self.assertFalse(self.agent.search_cache)

    async def test_real_empty_success_is_allowed_and_cached(self):
        response = httpx.Response(200, json={'code':200, 'data':{'webPages':{'value':[]}}}, request=httpx.Request('POST', 'https://example.test'))
        self.assertEqual(await self.invoke(response), [])
        self.assertTrue(self.agent.search_cache)


if __name__ == '__main__':
    unittest.main(verbosity=2)
