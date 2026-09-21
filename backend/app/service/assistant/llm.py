import json
import os
import asyncio
import logging
import time
import re
from contextvars import ContextVar
from uuid import uuid4
import httpx
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError
from config.llm_config import get_config

logger = logging.getLogger(__name__)
MODEL_CONTEXT = ContextVar('research_model_context', default={})


def supports_thinking_switch(model):
    """Match versioned family names, including deepseek-v4.1 and dated aliases."""
    name = model.lower()
    return name.startswith(('glm-', 'qwen3')) or bool(re.match(r'^deepseek-v4(?:[.\-]|$)', name))


def requires_thinking(model):
    # Provider-confirmed thinking-only models. Hybrid variants keep the user's setting.
    return model.lower() in {'glm-5.3', 'qwen3.8-2.4t-a95b'}


def validate_extraction(result):
    """有效 JSON 仍可能不符合检索协议；在同一重试预算内校验。"""
    facts = result.get('extracted_facts')
    if not isinstance(facts, list) or any(not isinstance(f, dict) or
            not isinstance(f.get('content'), str) or not isinstance(f.get('source_url'), str) for f in facts):
        raise ModelResponseError('事实抽取缺少 extracted_facts 列表或事实的 content/source_url 字段', retryable=True)
    for key in ('source_tracing_queries', 'follow_up_queries', 'further_tracing_queries', 'key_insights'):
        if key in result and (not isinstance(result[key], list) or any(not isinstance(x, str) for x in result[key])):
            raise ModelResponseError('事实抽取的查询/洞察列表格式无效', retryable=True)
    for key in ('data_points', 'entities_discovered', 'hypothesis_evidence'):
        if key in result and (not isinstance(result[key], list) or any(not isinstance(x, dict) for x in result[key])):
            raise ModelResponseError('事实抽取的实体/数据列表格式无效', retryable=True)
    for fact in facts:
        points = fact.get('data_points', [])
        if not isinstance(points, list) or any(not isinstance(x, dict) for x in points):
            raise ModelResponseError('事实中的数据点格式无效', retryable=True)


class ModelQuotaExhausted(RuntimeError):
    """可恢复的供应商额度阻塞，不包含密钥或请求内容。"""


class ModelCallTimedOut(RuntimeError):
    """单次请求超时，不限制整个研究任务的总时间。"""


class ModelResponseError(ValueError):
    """可向用户展示的返回格式错误，不含原始响应或凭据。"""
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class ModelResponseTruncated(ModelResponseError):
    """上层可针对任务缩短输出后重试；不能把截断 JSON 当作成功。"""


class ModelGateway:
    def __init__(self, usage=None):
        self.usage = usage if usage is not None else {}

    async def complete(self, system, prompt, structured=False, max_tokens=2500):
        seconds = float(os.getenv('RESEARCH_MODEL_CALL_TIMEOUT_SECONDS', '600'))
        try:
            return await asyncio.wait_for(self._complete_with_retry(system, prompt, structured, max_tokens), seconds)
        except asyncio.TimeoutError as exc:
            raise ModelCallTimedOut(f'本次模型调用（含重试）超过 {seconds:g} 秒，已保留完成步骤，可继续任务；这不是研究总时长限制。') from exc

    async def _complete_with_retry(self, system, prompt, structured, max_tokens):
        attempt_seconds = float(os.getenv('RESEARCH_MODEL_ATTEMPT_TIMEOUT_SECONDS',
                                         '180' if max_tokens <= 6000 else '600'))
        request_id = uuid4().hex[:12]
        for attempt in range(2):
            token = MODEL_CONTEXT.set({**MODEL_CONTEXT.get(), 'request_id': request_id, 'attempt': attempt + 1})
            try:
                return await asyncio.wait_for(self._complete(system, prompt, structured, max_tokens), attempt_seconds)
            except (ModelResponseError, asyncio.TimeoutError, APIConnectionError, APITimeoutError) as exc:
                if isinstance(exc, ModelResponseError) and not exc.retryable:
                    raise
                if attempt:
                    if isinstance(exc, asyncio.TimeoutError):
                        raise ModelCallTimedOut(f'模型单次尝试超过 {attempt_seconds:g} 秒，重试仍未完成；可从已保存步骤继续。') from exc
                    raise
                logger.warning('Retrying model attempt: reason=%s context=%s', type(exc).__name__, MODEL_CONTEXT.get())
                if structured and isinstance(exc, ModelResponseError):
                    prompt += ('\n格式修正：上一次响应为空或不符合 JSON 对象格式。'
                               '请按上文要求的字段返回最外层为 {...} 的 JSON 对象，不要返回 []、null 或字符串。'
                               '没有可提取证据时，对应列表字段填 []，说明字段写明未找到；不要编造内容。'
                               '\n具体校验问题：' + str(exc))
            finally:
                MODEL_CONTEXT.reset(token)

    async def _complete(self, system, prompt, structured=False, max_tokens=2500):
        started = time.monotonic()
        trace = {**MODEL_CONTEXT.get(), 'started_at': time.time(), 'max_tokens': max_tokens,
                 'status': 'running', 'first_chunk_seconds': None, 'first_content_seconds': None,
                 'content_chars': 0, 'reasoning_chars': 0}
        traces = self.usage.setdefault('model_attempts', [])
        traces.append(trace)
        del traces[:-200]
        try:
            result = await self._stream_complete(system, prompt, structured, max_tokens, trace, started)
            trace['status'] = 'completed'
            return result
        except asyncio.CancelledError:
            trace['status'] = 'cancelled_or_deadline'
            raise
        except Exception as exc:
            trace.update(status='failed', error_type=type(exc).__name__)
            raise
        finally:
            trace['duration_seconds'] = round(time.monotonic() - started, 3)
            logger.info('Model attempt finished: %s', json.dumps(trace, ensure_ascii=False))

    async def _stream_complete(self, system, prompt, structured, max_tokens, trace, started):
        config = get_config()
        # ContextVar 随 asyncio 任务隔离；并发 Scout 不得修改共享默认模型。
        selected_model = config.get_agent_config('scout').model if MODEL_CONTEXT.get().get('role') == 'scout' else config.default_model
        trace['model'] = selected_model
        configured_thinking = os.getenv('RESEARCH_ENABLE_THINKING', 'false').lower() == 'true'
        mandatory_thinking = requires_thinking(selected_model)
        trace['thinking_mode'] = 'required_by_model' if selected_model == 'kimi-k3' or mandatory_thinking else (
            'enabled' if configured_thinking else 'disabled') if supports_thinking_switch(selected_model) else 'provider_default'
        logger.info('Model request: model=%s, thinking_mode=%s, max_tokens=%s',
                    selected_model, trace['thinking_mode'], max_tokens)
        self.usage['model_calls'] = self.usage.get('model_calls', 0) + 1
        async with AsyncOpenAI(api_key=config.api_key, base_url=config.base_url,
                timeout=90, max_retries=0,
                http_client=httpx.AsyncClient(trust_env=os.getenv('LLM_TRUST_ENV', 'false').lower() == 'true')) as client:
            args = dict(model=selected_model, temperature=0.2, max_tokens=max_tokens,
                        messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}])
            # DashScope kimi-k3 rejects temperature=0.2; use the provider default.
            if selected_model == 'kimi-k3':
                args.pop('temperature')
            if supports_thinking_switch(selected_model):
                args['extra_body'] = {'enable_thinking': mandatory_thinking or configured_thinking}
            effort = MODEL_CONTEXT.get().get('reasoning_effort')
            if selected_model.lower() == 'qwen3.8-2.4t-a95b':
                # Qwen's provider default is xhigh; use low for bounded research calls.
                # Do not combine reasoning_effort with thinking_budget (provider rejects it).
                effort = {'high': 'xhigh', 'max': 'xhigh'}.get(effort, effort) or 'low'
                if effort not in ('low', 'medium', 'xhigh'):
                    effort = 'low'
                args['reasoning_effort'] = effort
                trace['reasoning_effort'] = effort
            elif selected_model.lower() == 'glm-5.3' and effort in ('low', 'high', 'max'):
                args['reasoning_effort'] = effort
                trace['reasoning_effort'] = effort
            if structured:
                args['response_format'] = {'type': 'json_object'}
                args['messages'][0]['content'] += ('\n请仅返回一个合法的 JSON 对象，最外层必须是 {...}，不可为数组、null 或字符串，不要添加 Markdown 代码围栏。'
                    '即使没有可用信息，也应保留用户要求的字段，在相应列表字段填空数组，并在说明字段说明缺口，不编造内容。')
                if MODEL_CONTEXT.get().get('response_schema') == 'research_extraction':
                    args['messages'][0]['content'] += ('\n顶层必须包含 extracted_facts 数组，每个事实必须含 content 和 source_url 字符串。'
                        '字段名使用请求中的英文名称，不得将整个结果放入 data/analysis 等包装字段；没有事实时返回 extracted_facts: []。')
            try:
                stream = await client.chat.completions.create(**args, stream=True, stream_options={'include_usage': True})
                parts, size, finish_reason, usage = [], 0, None, None
                async with stream:
                    async for chunk in stream:
                        elapsed = round(time.monotonic() - started, 3)
                        if trace['first_chunk_seconds'] is None:
                            trace['first_chunk_seconds'] = elapsed
                        trace['last_chunk_seconds'] = elapsed
                        if chunk.usage:
                            usage = chunk.usage
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        trace['reasoning_chars'] += len(getattr(choice.delta, 'reasoning_content', None) or '')
                        finish_reason = choice.finish_reason or finish_reason
                        trace['finish_reason'] = finish_reason
                        content = choice.delta.content
                        if isinstance(content, str):
                            if content and trace['first_content_seconds'] is None:
                                trace['first_content_seconds'] = elapsed
                            size += len(content)
                            trace['content_chars'] = size
                            if size > 200000:
                                raise ModelResponseError('模型正文超过输出上限，已保留完成步骤')
                            parts.append(content)
            except Exception as exc:
                body = getattr(exc, 'body', {})
                detail = body.get('error', body) if isinstance(body, dict) else {}
                if isinstance(detail, dict) and detail.get('code') == 'AllocationQuota.FreeTierOnly':
                    raise ModelQuotaExhausted('当前模型免费额度已耗尽；恢复可用额度后可从已保存步骤继续。') from exc
                if getattr(exc, 'status_code', None) == 400:
                    # Expose only an allowlisted diagnosis, not provider request bodies.
                    trace['provider_status'] = 400
                    if isinstance(detail, dict) and 'enable_thinking' in str(detail.get('message', '')):
                        raise ModelResponseError('当前模型的思考模式参数不兼容，请检查模型适配配置；已保留完成步骤。') from exc
                    raise ModelResponseError('模型供应商拒绝了请求参数（HTTP 400），请检查模型名称、参数支持和输入长度；已保留完成步骤。') from exc
                raise
        if usage:
            for key in ('prompt_tokens', 'completion_tokens'):
                amount = getattr(usage, key, 0) or 0
                trace[key] = amount
                self.usage[key] = self.usage.get(key, 0) + amount
            details = getattr(usage, 'completion_tokens_details', None)
            if details is not None:
                trace['reasoning_tokens'] = getattr(details, 'reasoning_tokens', None)
        if finish_reason == 'length':
            raise ModelResponseTruncated(f'本次模型输出被长度限制截断（请求上限 {max_tokens} Token），已保留完成步骤；这不是账户余额耗尽。')
        content = ''.join(parts)
        if not content.strip():
            raise ModelResponseError('模型没有返回正文，请从已完成步骤继续', retryable=True)
        if structured:
            # 只移除完整的 Markdown 外壳，不补括号、改数值或猜测残缺 JSON。
            fenced = re.fullmatch(r'\s*```(?:json)?\s*\n?([\s\S]*?)\n?```\s*', content, re.IGNORECASE)
            if fenced:
                content = fenced.group(1).strip()
                trace['removed_code_fence'] = True
            try:
                result = json.loads(content)
            except json.JSONDecodeError as exc:
                trace['json_error'] = {'message': exc.msg, 'line': exc.lineno, 'column': exc.colno,
                                       'has_code_fence': content.lstrip().startswith('```')}
                raise ModelResponseError('模型未返回合法 JSON，已保留完成步骤，可重试当前步骤', retryable=True) from exc
            if not isinstance(result, dict):
                raise ModelResponseError('模型返回了无效的结构化结果，已保留完成步骤', retryable=True)
            if MODEL_CONTEXT.get().get('response_schema') == 'research_extraction':
                trace['response_fields'] = [key if re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,63}', key)
                                            else '<invalid-field-name>' for key in sorted(result.keys())[:30]]
                validate_extraction(result)
            return result
        return content
