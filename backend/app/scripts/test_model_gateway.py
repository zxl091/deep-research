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
        for model in ('qwen3.7-flash', 'qwen3.8-27b', 'qwen3.5-plus', 'deepseek-v4.1-flash', 'deepseek-v4.2-pro-1001', 'deepseek-v4-flash-0731'):
            for enabled in ('false', 'true'):
                with self.subTest(model=model, enabled=enabled), patch.dict(llm.os.environ, {'RESEARCH_ENABLE_THINKING':enabled}):
                    await self.invoke([chunk('ok', 'stop')], model=model)
                    self.assertEqual(self.last_request['extra_body']['enable_thinking'], enabled == 'true')

    async def test_unknown_family_does_not_receive_thinking_parameter(self):
        await self.invoke([chunk('ok', 'stop')], model='some-other-model')
        self.assertNotIn('extra_body', self.last_request)

    async def test_glm53_requires_thinking_without_changing_other_models(self):
        with patch.dict(llm.os.environ, {'RESEARCH_ENABLE_THINKING': 'false'}):
            _, usage = await self.invoke([chunk('ok', 'stop')], model='glm-5.3')
            self.assertTrue(self.last_request['extra_body']['enable_thinking'])
            self.assertEqual(usage['model_attempts'][0]['thinking_mode'], 'required_by_model')
            for model in ('glm-5.2', 'deepseek-v4.1-flash', 'qwen3.8-27b'):
                await self.invoke([chunk('ok', 'stop')], model=model)
                self.assertFalse(self.last_request['extra_body']['enable_thinking'])

    async def test_parameter_error_is_actionable_without_leaking_provider_body(self):
        error = RuntimeError('private provider body')
        error.status_code = 400
        error.body = {'error': {'message': 'enable_thinking must be true; private prompt text'}}
        with self.assertRaises(llm.ModelResponseError) as caught:
            await self.invoke(error=error)
        self.assertIn('思考模式参数不兼容', str(caught.exception))
        self.assertNotIn('private', str(caught.exception))
        error.body = {'error': {'message': 'private input exceeds limit'}}
        with self.assertRaisesRegex(llm.ModelResponseError, 'HTTP 400') as caught:
            await self.invoke(error=error)
        self.assertNotIn('private', str(caught.exception))

    async def test_glm_effort_is_scoped_to_chart_call(self):
        token = llm.MODEL_CONTEXT.set({'step': 'chart_extraction', 'reasoning_effort': 'low'})
        try:
            _, usage = await self.invoke([chunk('ok', 'stop')], model='glm-5.3')
            self.assertEqual(self.last_request['reasoning_effort'], 'low')
            self.assertEqual(usage['model_attempts'][0]['reasoning_effort'], 'low')
            await self.invoke([chunk('ok', 'stop')], model='deepseek-v4.1-flash')
            self.assertNotIn('reasoning_effort', self.last_request)
        finally: llm.MODEL_CONTEXT.reset(token)
        await self.invoke([chunk('ok', 'stop')], model='glm-5.3')
        self.assertNotIn('reasoning_effort', self.last_request)

    async def test_usage_records_provider_reasoning_tokens(self):
        _, usage = await self.invoke([chunk('ok', 'stop'), NS(choices=[], usage=NS(
            prompt_tokens=10, completion_tokens=20, completion_tokens_details=NS(reasoning_tokens=15)))])
        trace = usage['model_attempts'][0]
        self.assertEqual((trace['completion_tokens'], trace['reasoning_tokens']), (20, 15))

    async def test_qwen_thinking_only_model_uses_valid_bounded_effort(self):
        with patch.dict(llm.os.environ, {'RESEARCH_ENABLE_THINKING': 'false'}):
            _, usage = await self.invoke([chunk('ok', 'stop')], model='qwen3.8-2.4t-a95b')
            self.assertTrue(self.last_request['extra_body']['enable_thinking'])
            self.assertEqual(self.last_request['reasoning_effort'], 'low')
            self.assertNotIn('thinking_budget', self.last_request['extra_body'])
            self.assertEqual(usage['model_attempts'][0]['thinking_mode'], 'required_by_model')
            token = llm.MODEL_CONTEXT.set({'reasoning_effort': 'high'})
            try:
                await self.invoke([chunk('ok', 'stop')], model='qwen3.8-2.4t-a95b')
                self.assertEqual(self.last_request['reasoning_effort'], 'xhigh')
            finally: llm.MODEL_CONTEXT.reset(token)
            await self.invoke([chunk('ok', 'stop')], model='qwen3.8-27b')
            self.assertFalse(self.last_request['extra_body']['enable_thinking'])
            self.assertNotIn('reasoning_effort', self.last_request)

    async def test_kimi_k3_uses_provider_temperature_default(self):
        _, usage = await self.invoke([chunk('{"ok":true}', 'stop')], structured=True, model='kimi-k3')
        self.assertEqual(usage['model_attempts'][0]['thinking_mode'], 'required_by_model')
        self.assertNotIn('temperature',self.last_request)
        self.assertEqual(self.last_request['response_format'],{'type':'json_object'})
        await self.invoke([chunk('ok', 'stop')],model='deepseek-v4-pro-0813')
        self.assertEqual(self.last_request['temperature'],.2)

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
            with self.assertRaisesRegex(llm.ModelCallTimedOut, '模型调用'):
                await llm.ModelGateway().complete('system', 'prompt')
        self.assertTrue(closed.is_set())
        client.__aexit__.assert_awaited_once()

    async def test_structured_mode_adds_json_instruction_for_every_caller(self):
        await self.invoke([chunk('{"ok":true}', 'stop')], structured=True, model='qwen3.7-flash')
        self.assertEqual(self.last_request['response_format'], {'type':'json_object'})
        self.assertIn('JSON', self.last_request['messages'][0]['content'])

    async def test_valid_json_wrong_extraction_shape_is_retried_not_accepted(self):
        token = llm.MODEL_CONTEXT.set({'response_schema':'research_extraction'})
        try:
            with self.assertRaisesRegex(llm.ModelResponseError, 'extracted_facts'):
                await self.invoke([chunk('{"analysis": {"facts": []}}', 'stop')], structured=True)
            result, _ = await self.invoke([chunk('{"extracted_facts":[]}', 'stop')], structured=True)
            self.assertEqual(result, {'extracted_facts':[]})
            self.assertIn('source_url', self.last_request['messages'][0]['content'])
        finally:
            llm.MODEL_CONTEXT.reset(token)

    async def test_complete_json_fence_is_unwrapped_without_second_request(self):
        result, usage = await self.invoke([chunk('```json\n{"ok":true}\n```', 'stop')], structured=True)
        self.assertEqual(result, {'ok':True})
        self.assertEqual(usage['model_calls'], 1)
        self.assertTrue(usage['model_attempts'][0]['removed_code_fence'])
        with self.assertRaises(llm.ModelResponseError):
            await self.invoke([chunk('```json\n{"ok":\n```', 'stop')], structured=True)

    async def test_invalid_field_names_do_not_leak_response_body_into_diagnostics(self):
        token = llm.MODEL_CONTEXT.set({'response_schema':'research_extraction'})
        try:
            result, usage = await self.invoke([chunk('{"extracted_facts":[], "private body text":"ignored"}', 'stop')], structured=True)
            self.assertIn('<invalid-field-name>', usage['model_attempts'][0]['response_fields'])
            self.assertNotIn('private body text', str(usage))
        finally:
            llm.MODEL_CONTEXT.reset(token)

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
        self.assertEqual({k: usage[k] for k in ('model_calls','prompt_tokens','completion_tokens')},
                         {'model_calls': 1, 'prompt_tokens': 12, 'completion_tokens': 5})
        trace = usage['model_attempts'][0]
        self.assertEqual(trace['status'], 'completed')
        self.assertIsNotNone(trace['first_content_seconds'])
        self.assertEqual(trace['content_chars'], len('{"ok":true}'))
        self.assertNotIn('不应出现在正文', str(trace))

    async def test_attempt_timeout_retries_with_a_fresh_attempt_budget(self):
        gateway = llm.ModelGateway()
        attempts = []
        async def request(*args):
            attempts.append(True)
            if len(attempts) == 1: await asyncio.sleep(1)
            return {'ok':True}
        with patch.dict(llm.os.environ, {'RESEARCH_MODEL_CALL_TIMEOUT_SECONDS':'.3',
                                        'RESEARCH_MODEL_ATTEMPT_TIMEOUT_SECONDS':'.03'}), \
             patch.object(gateway, '_complete', side_effect=request):
            self.assertEqual(await gateway.complete('s', 'p', True), {'ok':True})
        self.assertEqual(len(attempts), 2)

    async def test_total_budget_covers_both_attempts(self):
        gateway = llm.ModelGateway()
        attempts = []
        async def request(*args):
            attempts.append(True)
            if len(attempts) == 1: raise asyncio.TimeoutError()
            await asyncio.sleep(1)
        with patch.dict(llm.os.environ, {'RESEARCH_MODEL_CALL_TIMEOUT_SECONDS':'.08',
                                        'RESEARCH_MODEL_ATTEMPT_TIMEOUT_SECONDS':'.5'}), \
             patch.object(gateway, '_complete', side_effect=request) as call:
            with self.assertRaises(llm.ModelCallTimedOut): await gateway.complete('s', 'p', True)
        self.assertEqual(call.await_count, 2)

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
