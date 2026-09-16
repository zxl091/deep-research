import json
import os
import asyncio
import logging
import httpx
from openai import AsyncOpenAI
from config.llm_config import get_config

logger = logging.getLogger(__name__)


class ModelQuotaExhausted(RuntimeError):
    """可恢复的供应商额度阻塞，不包含密钥或请求内容。"""


class ModelCallTimedOut(RuntimeError):
    """单次请求超时，不限制整个研究任务的总时间。"""


class ModelResponseError(ValueError):
    """可向用户展示的返回格式错误，不含原始响应或凭据。"""
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class ModelGateway:
    def __init__(self, usage=None):
        self.usage = usage if usage is not None else {}

    async def complete(self, system, prompt, structured=False, max_tokens=2500):
        seconds = float(os.getenv('RESEARCH_MODEL_CALL_TIMEOUT_SECONDS', '600'))
        try:
            return await asyncio.wait_for(self._complete_with_retry(system, prompt, structured, max_tokens), seconds)
        except asyncio.TimeoutError as exc:
            raise ModelCallTimedOut(f'单次模型请求超过 {seconds:g} 秒，已保留完成步骤，可继续任务；这不是研究总时长限制。') from exc

    async def _complete_with_retry(self, system, prompt, structured, max_tokens):
        for attempt in range(2):
            try:
                return await self._complete(system, prompt, structured, max_tokens)
            except ModelResponseError as exc:
                if attempt or not exc.retryable:
                    raise
                logger.warning('Retrying one empty or malformed model response: %s', str(exc))
                if structured:
                    prompt += ('\n格式修正：上一次响应为空或不符合 JSON 对象格式。'
                               '请按上文要求的字段返回最外层为 {...} 的 JSON 对象，不要返回 []、null 或字符串。'
                               '没有可提取证据时，对应列表字段填 []，说明字段写明未找到；不要编造内容。')

    async def _complete(self, system, prompt, structured=False, max_tokens=2500):
        config = get_config()
        logger.info('Model request: model=%s, configured_thinking=%s, max_tokens=%s',
                    config.default_model, os.getenv('RESEARCH_ENABLE_THINKING', 'false'), max_tokens)
        self.usage['model_calls'] = self.usage.get('model_calls', 0) + 1
        async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                timeout=90, max_retries=1,
                http_client=httpx.AsyncClient(trust_env=os.getenv('LLM_TRUST_ENV', 'false').lower() == 'true')) as client:
            args = dict(model=config.default_model, temperature=0.2, max_tokens=max_tokens,
                        messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}])
            if config.default_model.startswith(('glm-', 'deepseek-v4-', 'qwen3')):
                args['extra_body'] = {'enable_thinking': os.getenv('RESEARCH_ENABLE_THINKING', 'false').lower() == 'true'}
            if structured:
                args['response_format'] = {'type': 'json_object'}
                args['messages'][0]['content'] += ('\n请仅返回一个合法的 JSON 对象，最外层必须是 {...}，不可为数组、null 或字符串，不要添加 Markdown 代码围栏。'
                    '即使没有可用信息，也应保留用户要求的字段，在相应列表字段填空数组，并在说明字段说明缺口，不编造内容。')
            try:
                stream = await client.chat.completions.create(**args, stream=True, stream_options={'include_usage': True})
                parts, size, finish_reason, usage = [], 0, None, None
                async with stream:
                    async for chunk in stream:
                        if chunk.usage:
                            usage = chunk.usage
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        finish_reason = choice.finish_reason or finish_reason
                        content = choice.delta.content
                        if isinstance(content, str):
                            size += len(content)
                            if size > 200000:
                                raise ModelResponseError('模型正文超过输出上限，已保留完成步骤')
                            parts.append(content)
            except Exception as exc:
                body = getattr(exc, 'body', {})
                detail = body.get('error', body) if isinstance(body, dict) else {}
                if isinstance(detail, dict) and detail.get('code') == 'AllocationQuota.FreeTierOnly':
                    raise ModelQuotaExhausted('当前模型免费额度已耗尽；恢复可用额度后可从已保存步骤继续。') from exc
                raise
        if usage:
            for key in ('prompt_tokens', 'completion_tokens'):
                self.usage[key] = self.usage.get(key, 0) + getattr(usage, key, 0)
        if finish_reason == 'length':
            raise ModelResponseError('模型正文达到输出额度而被截断，已保留完成步骤')
        content = ''.join(parts)
        if not content.strip():
            raise ModelResponseError('模型没有返回正文，请从已完成步骤继续', retryable=True)
        if structured:
            try:
                result = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ModelResponseError('模型未返回合法 JSON，已保留完成步骤，可重试当前步骤', retryable=True) from exc
            if not isinstance(result, dict):
                raise ModelResponseError('模型返回了无效的结构化结果，已保留完成步骤', retryable=True)
            return result
        return content
