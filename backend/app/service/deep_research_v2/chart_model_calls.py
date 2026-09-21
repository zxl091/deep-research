"""Checkpointed, bounded model requests for chart planning and evidence extraction."""
import hashlib
import json

from service.assistant.llm import MODEL_CONTEXT, ModelResponseTruncated


async def cached_call(deep, model, system, prompt, tokens, step, progress=None):
    cache = deep.setdefault('chart_model_checkpoints', {})
    key = hashlib.sha256(json.dumps([system, prompt, tokens, step], ensure_ascii=False).encode()).hexdigest()
    if key in cache:
        return cache[key]
    context = MODEL_CONTEXT.set({**MODEL_CONTEXT.get(), 'step': step, 'reasoning_effort': 'low'})
    try:
        result = await model.complete(system, prompt, True, tokens)
        cache[key] = result
        if progress:
            await progress('图表模型子步骤已保存：' + step)
        return result
    finally:
        MODEL_CONTEXT.reset(context)


async def plan_request(deep, model, system, prompt, progress=None):
    try:
        return await cached_call(deep, model, system, prompt, 4000, 'chart_plan', progress)
    except ModelResponseTruncated:
        if progress: await progress('图表规划输出截断，精简计划后重试一次')
        return await cached_call(deep, model, system,
            prompt + '\n上次输出截断。只保留图表计划 JSON，每项解释最多80字，最多6项，不重复原文。',
            10000, 'chart_plan_retry', progress)


def source_groups(sources, limit=9000):
    batch, size = [], 0
    for source in sources.values():
        count = len(json.dumps(source, ensure_ascii=False))
        if batch and size + count > limit:
            yield batch
            batch, size = [], 0
        batch.append(source)
        size += count
    if batch: yield batch


async def extraction_request(deep, model, system, prefix, sources, progress=None):
    prompt = prefix + '\n原始摘录：' + json.dumps(list(sources.values()), ensure_ascii=False)
    failures = deep.setdefault('chart_model_recovery', {})
    key = hashlib.sha256(prompt.encode()).hexdigest()
    if key not in failures:
        try:
            return await cached_call(deep, model, system, prompt, 10000, 'chart_extraction', progress)
        except ModelResponseTruncated:
            failures[key] = {'reason': 'length', 'recovery': 'source_batches'}
            if progress: await progress('图表抽取输出截断，按来源拆批恢复；保留全部已选证据')
    charts, missing, queries, failed_batches = [], [], [], 0
    groups = list(source_groups(sources))
    for index, group in enumerate(groups):
        batch_prompt = (prefix + '\n现在只提取这一批来源中属于该图的数据。允许只有一个点，不为补齐整图猜测。'
                        '不重新规划、不重复思考其他来源是否可用，稍后由程序合并并核验。'
                        '\n原始摘录：' + json.dumps(group, ensure_ascii=False))
        for attempt, budget in enumerate((10000, 16000)):
            try:
                response = await cached_call(deep, model, system,
                    batch_prompt + ('\n上次输出截断：只返回必要字段、最短可核验引文和完整JSON。' if attempt else ''),
                    budget, 'chart_extraction_batch', progress)
                break
            except ModelResponseTruncated:
                if attempt:
                    response = {'chart': None, 'missing_data': [f'第{index + 1}批图表来源的模型输出仍截断，未据此补值。']}
                    failed_batches += 1
        if isinstance(response.get('chart'), dict):
            if isinstance(response['chart'].get('points'), list):
                charts.append(response['chart'])
            else:
                failed_batches += 1
                missing.append(f'第{index + 1}批图表数据点格式无效，未用于绘图。')
        missing.extend(x for x in response.get('missing_data', []) if isinstance(x, str))
        queries.extend(x for x in response.get('search_queries', []) if isinstance(x, str))
    # A model-generated partial series is only a proposal. The caller must still
    # validate every point and the complete series against the original evidence.
    chart = dict(charts[0], points=[]) if charts else None
    seen = set()
    for candidate in charts:
        for point in candidate.get('points', []):
            identity = json.dumps(point, sort_keys=True, ensure_ascii=False)
            if identity not in seen:
                chart['points'].append(point)
                seen.add(identity)
    failures[key].update(total_batches=len(groups), failed_batches=failed_batches)
    return {'chart': chart, 'missing_data': list(dict.fromkeys(missing)),
            'search_queries': list(dict.fromkeys(queries)), 'model_output_incomplete': bool(failed_batches)}
