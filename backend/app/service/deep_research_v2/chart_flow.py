"""数据台账 → 同口径选图 → 固定模板 → 沙箱；独立记录证据/解析/渲染失败。"""
import asyncio
import copy

from .chart_contract import render_code
from .chart_inventory import VERSION, choose_charts, extract_inventory, verify_records
from .evidence_records import explicit_enumeration_records


async def grounded_charts(deep, snapshots, model, execute_code, *, supplement=None, progress=None,
                          fetch_source=None, reuse_proposals=False, replay_saved=False):
    from .chart_pipeline import POINT_SCHEMA, select_sources, reusable
    async def emit(message):
        if progress:
            await progress(message)
    previous = reusable(deep.get('charts', []), snapshots)
    # 旧计划只能帮助挑选相关来源，不能决定图型或迫使补足预想点数。
    preferred = list(dict.fromkeys(u for p in deep.get('chart_plan', []) for u in p.get('source_urls', [])))
    selected = select_sources(deep, snapshots, {'source_urls': preferred, 'data_question': deep['query']}, max_chars=36000)
    validation = {'version': VERSION, 'accepted': 0, 'rejected': [], 'items': [], 'fetches': [], 'searches': []}
    deep['chart_validation'] = validation
    saved = deep.get('chart_evidence_records', {})
    old_records = saved.get('records', [])
    if replay_saved:
        proposals = copy.deepcopy(saved.get('candidate_records', [r['point'] for r in saved.get('rejected', [])]))
        batches = copy.deepcopy(saved.get('batches', []))
        await emit('离线证据复核：复用已保存模型响应，不调用模型或联网')
    else:
        proposals, batches = await extract_inventory(deep, selected, snapshots, model, POINT_SCHEMA, emit)
    # 只保留通过当前来源重新核验的旧记录，不信任过去的“verified”标记。
    proposals = [r['point'] for r in old_records if r['point'].get('extraction_method') != 'explicit_enumeration'] + [
        p for p in proposals if p.get('extraction_method') != 'explicit_enumeration'] + list(explicit_enumeration_records(snapshots))
    records, rejected = verify_records(proposals, snapshots)
    # 解析问题只回读原网页，不盲目扩大搜索；不同表格版本不能覆盖既有引用。
    if fetch_source and not replay_saved:
        urls = list(dict.fromkeys(x['point'].get('source_url') for x in rejected
            if x['status'] in ('parse_failed', 'source_missing') and x['point'].get('source_url') in snapshots))[:2]
        for url in urls:
            entry = {'url': url}
            validation['fetches'].append(entry)
            cache = deep.setdefault('chart_source_fetches', {})
            if url in cache:
                entry.update(cache[url])
                continue
            try:
                await emit('图表证据结构回读：' + url[:100])
                fetched = await asyncio.wait_for(fetch_source(url), 25)
                old = snapshots[url]
                # 保留原摘要的定位；完整正文单独添加，避免每次重试重复叠加。
                if fetched['content'] not in old['content']:
                    old['content'] += '\n\n' + fetched['content']
                if not old.get('tables'):
                    old['tables'] = fetched.get('tables', [])
                entry.update(status='completed', tables=len(fetched.get('tables', [])))
            except Exception as exc:
                entry.update(status='source_fetch_failed', error=type(exc).__name__)
            cache[url] = dict(entry)
        records, rejected = verify_records(proposals, snapshots)
    specs, table_only, selection_issues = choose_charts(records, snapshots)
    # 仅在确实缺少来源且尚不能成图时，按明确的字段缺口补查，最多两次。
    missing = [m for b in batches for m in b.get('missing_data', [])
               if isinstance(m, dict) and m.get('reason') == 'source_missing' and m.get('field') and m.get('query')]
    if not specs and supplement and not replay_saved:
        searched = deep.setdefault('chart_inventory_searches', [])
        fresh = []
        for m in missing:
            query = str(m['query']).strip()[:300]
            if query in searched or len(searched) >= 2:
                continue
            searched.append(query)
            entry = {'query': query, 'field': m['field']}
            validation['searches'].append(entry)
            try:
                fresh.extend(await supplement(query, None) or [])
                entry['status'] = 'completed'
            except Exception as exc:
                entry.update(status='source_fetch_failed', error=type(exc).__name__)
        if fresh:
            more = select_sources(deep, {u:snapshots[u] for u in set(fresh) if u in snapshots}, max_chars=12000)
            additions, extra_batches = await extract_inventory(deep, more, snapshots, model, POINT_SCHEMA, emit)
            proposals.extend(additions)
            batches.extend(extra_batches)
            records, rejected = verify_records(proposals, snapshots)
            specs, table_only, selection_issues = choose_charts(records, snapshots)
    ledger = {'version': VERSION, 'records': records, 'rejected': rejected, 'batches': batches, 'table_only': table_only,
              'candidate_records':proposals, 'replayed_without_model':replay_saved}
    deep['chart_evidence_records'] = ledger
    deep['chart_plan'] = [{'id':s['id'], 'title':s['title'], 'type':s['type'], 'desired_points':len(s['points']),
                          'source_urls':list(dict.fromkeys(p['source_url'] for p in s['points'])),
                          'evidence_ids':s['evidence_ids']} for s in specs]
    charts = list(previous)
    deep['charts'] = charts
    for spec in specs:
        item = {'plan_id':spec['id'], 'title':spec['title'], 'points':len(spec['points']), 'status':'rendering'}
        validation['items'].append(item)
        retained = next((c for c in previous if c.get('plan_id') == spec['id'] and c.get('data_contract') == spec), None)
        if retained:
            item['status'] = 'retained'
        else:
            await emit('图表渲染：' + spec['title'])
            try:
                output = await execute_code(render_code(spec))
                if not output.get('success') or not output.get('charts'):
                    raise ValueError(str(output.get('error') or '沙箱未生成图片')[:300])
                charts[:] = [c for c in charts if c.get('plan_id') != spec['id']]
                charts.append({'id':spec['id'], 'plan_id':spec['id'], 'title':spec['title'],
                    'chart_type':spec['type'], 'image_base64':output['charts'][0],
                    'data_contract':copy.deepcopy(spec), 'verified_data':True})
                item['status'] = 'completed'
            except Exception as exc:
                item.update(status='render_failed', errors=[str(exc)[:300]])
        validation['accepted'] = len(charts)
        await emit('图表步骤已保存：' + spec['title'] + '（' + item['status'] + '）')
    # 合法单点不是执行错误，也不会因此要求模型再编一条数据。
    for record in records:
        if record['id'] in table_only:
            p = record['point']
            validation['items'].append({'plan_id':record['id'], 'title':p['entity']+' · '+p['metric'],
                'status':'table_only', 'points':1, 'errors':['已核验，暂无同来源同口径的可比较数据；保留在数据台账。']})
    for i, rejection in enumerate(rejected):
        p = rejection['point']
        validation['items'].append({'plan_id':f'data_issue_{i}', 'title':str(p.get('entity', '数据记录'))+' · '+str(p.get('metric', '')),
            'status':rejection['status'], 'errors':[rejection['message']]})
    validation['items'].extend(dict(x, plan_id=f'selection_{i}') for i,x in enumerate(selection_issues))
    for i, batch in enumerate(batches):
        if batch['status'] == 'extraction_failed':
            validation['items'].append({'plan_id':f'batch_{i}', 'title':f'来源抽取第 {i+1} 批',
                'status':'extraction_failed', 'errors':[batch['error']]})
    validation.update(accepted=len(charts), verified_records=len(records), table_only=len(table_only), rejected_records=len(rejected))
    validation['rejected'] = [r['message'] for r in rejected]
    legacy = {'图表数据原句或口径未通过校验，未发布未经核对的图表。',
              '部分图表来源抽取因模型输出截断未全部完成，已保留核验通过的成果。',
              '部分图表抽取或沙箱渲染失败；已保留核验通过的数据与图表。'}
    deep['errors'] = [e for e in deep.get('errors', []) if e not in legacy]
    if any(i['status'] in ('extraction_failed', 'render_failed') for i in validation['items']):
        deep['errors'].append('部分图表抽取或沙箱渲染失败；已保留核验通过的数据与图表。')
    await emit(f'图表流程完成：{len(charts)} 张图，{len(records)} 条核验数据，{len(table_only)} 条仅保留为表格')
