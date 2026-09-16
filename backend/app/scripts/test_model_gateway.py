"""流式模型接口回归：正文拼接、usage 尾块、截断与额度错误。"""
import sys
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from service.assistant import llm


class Stream:
    def __init__(self, chunks): self.chunks = chunks
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def __aiter__(self): return self.iterate()
    async def iterate(self):
        for chunk in self.chunks: yield chunk


def chunk(content=None, finish=None):
    return NS(usage=None, choices=[NS(delta=NS(content=content, reasoning_content='不应出现在正文'), finish_reason=finish)])


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, chunks=None, error=None, structured=False, model='deepseek-v4-flash-0731'):
        create = AsyncMock(return_value=Stream(chunks or []), side_effect=error)
        client = AsyncMock()
        client.__aenter__.return_value = NS(chat=NS(completions=NS(create=create)))
        usage = {}
        config = NS(api_key='fixture', base_url='https://example.test', default_model=model)
        with patch.object(llm, 'get_config', return_value=config), patch.object(llm, 'AsyncOpenAI', return_value=client), patch.object(llm.httpx, 'AsyncClient'):
            result = await llm.ModelGateway(usage).complete('system', 'prompt', structured)
        self.assertTrue(create.call_args.kwargs['stream'])
        self.last_request = create.call_args.kwargs
        return result, usage

    async def test_qwen_honors_existing_thinking_setting(self):
        for model in ('qwen3.7-flash', 'qwen3.8-27b', 'qwen3.5-plus'):
            for enabled in ('false', 'true'):
                with self.subTest(model=model, enabled=enabled), patch.dict(llm.os.environ, {'RESEARCH_ENABLE_THINKING':enabled}):
                    await self.invoke([chunk('ok', 'stop')], model=model)
                    self.assertEqual(self.last_request['extra_body']['enable_thinking'], enabled == 'true')

    async def test_continuous_chunks_do_not_bypass_request_deadline(self):
        closed = asyncio.Event()
        class ContinuousStream(Stream):
            async def iterate(self):
                while True:
                    await asyncio.sleep(.005)
                    yield chunk()
            async def __aexit__(self, *args): closed.set()
        create = AsyncMock(return_value=ContinuousStream([]))
        client = AsyncMock()
        client.__aenter__.return_value = NS(chat=NS(completions=NS(create=create)))
        config = NS(api_key='fixture', base_url='https://example.test', default_model='qwen3.8-27b')
        with patch.dict(llm.os.environ, {'RESEARCH_MODEL_CALL_TIMEOUT_SECONDS':'.04'}), \
             patch.object(llm, 'get_config', return_value=config), patch.object(llm, 'AsyncOpenAI', return_value=client), patch.object(llm.httpx, 'AsyncClient'):
            with self.assertRaisesRegex(llm.ModelCallTimedOut, '单次模型请求'):
                await llm.ModelGateway().complete('system', 'prompt')
        self.assertTrue(closed.is_set())
        client.__aexit__.assert_awaited_once()

    async def test_structured_mode_adds_json_instruction_for_every_caller(self):
        await self.invoke([chunk('{"ok":true}', 'stop')], structured=True, model='qwen3.7-flash')
        self.assertEqual(self.last_request['response_format'], {'type':'json_object'})
        self.assertIn('JSON', self.last_request['messages'][0]['content'])

    async def test_one_empty_response_retry_then_success(self):
        gateway = llm.ModelGateway()
        with patch.object(gateway, '_complete', AsyncMock(side_effect=[
                llm.ModelResponseError('模型没有返回正文', retryable=True), {'ok':True}])) as call:
            self.assertEqual(await gateway.complete('system', 'prompt', True), {'ok':True})
            self.assertEqual(call.await_count, 2)
            self.assertIn('格式修正', call.call_args_list[1].args[1])
            self.assertIn('不要编造', call.call_args_list[1].args[1])

    async def test_bad_responses_retry_once_only_and_truncation_is_not_retried(self):
        for retryable, count in ((True, 2), (False, 1)):
            gateway = llm.ModelGateway()
            with patch.object(gateway, '_complete', AsyncMock(side_effect=llm.ModelResponseError('fixture', retryable))) as call:
                with self.assertRaises(llm.ModelResponseError):
                    await gateway.complete('system', 'prompt', True)
                self.assertEqual(call.await_count, count)

    async def test_json_content_and_usage_only_tail(self):
        result, usage = await self.invoke([chunk(), chunk('{"ok":'), chunk('true}', 'stop'),
            NS(choices=[], usage=NS(prompt_tokens=12, completion_tokens=5))], structured=True)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(usage, {'model_calls': 1, 'prompt_tokens': 12, 'completion_tokens': 5})

    async def test_truncated_report_is_not_success(self):
        with self.assertRaisesRegex(ValueError, '截断'):
            await self.invoke([chunk('未完成的报告', 'length')])

    async def test_quota_maps_to_recoverable_error(self):
        error = RuntimeError('provider error')
        error.body = {'error': {'code': 'AllocationQuota.FreeTierOnly'}}
        with self.assertRaises(llm.ModelQuotaExhausted):
            await self.invoke(error=error)

    async def test_empty_or_wrong_json_fails(self):
        for content in ['', '[]']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                await self.invoke([chunk(content, 'stop')], structured=True)


if __name__ == '__main__': unittest.main(verbosity=2)
