"""Checkpointed, bounded model requests for evidence inventory extraction."""
import hashlib
import json

from service.assistant.llm import MODEL_CONTEXT


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
