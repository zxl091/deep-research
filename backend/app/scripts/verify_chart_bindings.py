"""跨资料验收；模型输出用固定候选回放，不调用付费模型、不写业务库。"""
import argparse
import asyncio
import base64
import copy
from datetime import datetime
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend/app'))
from service.deep_research_v2.chart_contract import validate_charts
from service.deep_research_v2.chart_evidence import structured_html
from service.deep_research_v2.chart_pipeline import grounded_charts
from service.deep_research_v2.sandbox import execute_isolated


def materialize(case, body=None):
    content, tables = structured_html(body or case['html'])
    url = 'https://offline.fixture/' + str(case['name'])
    source = {url: dict(url=url, content=content, tables=tables)}
    points = [dict(entity=expected['entity'], label=expected['entity'], value=expected['value'],
        metric=case['metric'], unit=case['unit'], period=case['period'], period_basis=case['period_basis'],
        scope='虚构验收样本', value_kind=case['kind'], source_url=url,
        quote=' | '.join(tables[0]['rows'][row]),
        table_ref={'id':'table_0','row':row,'column':case['value_column']})
        for row, expected in enumerate(case['expected'], 1)]
    return dict(type=case['type'],title=case['name'],points=points), source


async def main(render=False):
    cases = json.loads(Path(__file__).with_name('fixtures').joinpath('chart_binding_acceptance.json').read_text(encoding='utf-8'))
    folder = ROOT / '.runtime' / ('chart-binding-acceptance-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True)
    results=[];counts={'correct_accepted':0,'wrong_rejected':0,'missing_rejected':0,'transformations_preserved':0,'sandbox_charts':0}
    for case in cases:
        chart, sources = materialize(case)
        valid, errors = validate_charts([chart], sources)
        assert valid and not errors, (case['name'], errors)
        for actual, expected in zip(valid[0]['points'], case['expected']):
            observation=actual['observation']
            assert observation['entity']==expected['entity'] and observation['value']==expected['value']
            assert observation['period']['start']==expected['start'] and observation['period']['end']==expected['end']
        counts['correct_accepted']+=1
        for mutation in ('entity','value','metric','period','unit','kind'):
            bad=copy.deepcopy(chart)
            first=bad['points'][0]
            if mutation=='entity':first['entity']=first['label']=bad['points'][1]['entity']
            if mutation=='value':first['value']=bad['points'][1]['value']
            if mutation=='metric':first['metric']='不存在的指标'
            if mutation=='period':first.update(period='2033',period_basis='全年')
            if mutation=='unit':first['unit']='未定义单位'
            if mutation=='kind':first['value_kind']='target'
            assert not validate_charts([bad],sources)[0],(case['name'],mutation)
            counts['wrong_rejected']+=1
        for missing in ('entity','header'):
            absent=copy.deepcopy(sources)
            table=next(iter(absent.values()))['tables'][0]
            if missing=='entity':table['rows'][0][0]='字段未说明'
            else:table['header_rows']=0
            assert not validate_charts([chart],absent)[0],(case['name'],missing)
            counts['missing_rejected']+=1
        spaced, spaced_sources=materialize(case,case['html'].replace('</td>',' </td>\n'))
        assert validate_charts([spaced],spaced_sources)[0]
        counts['transformations_preserved']+=1
        swapped=copy.deepcopy(sources); swapped_chart=copy.deepcopy(chart)
        next(iter(swapped.values()))['tables'][0]['rows'][1:]=list(reversed(next(iter(swapped.values()))['tables'][0]['rows'][1:]))
        for point in swapped_chart['points']:point['table_ref']['row']=3-point['table_ref']['row']
        assert validate_charts([swapped_chart],swapped)[0]
        counts['transformations_preserved']+=1
        if render:
            class Replay:
                async def complete(self,*args):return {'chart':chart}
            deep={'query':case['name'],'outline':[{'id':'s1','title':case['name']}],'charts':[],'facts':[],
                  'chart_plan':[{'id':'a','title':case['name'],'type':case['type'],'desired_points':2,'section_id':'s1'}]}
            await grounded_charts(deep,sources,Replay(),execute_isolated)
            assert len(deep['charts'])==1,deep['chart_validation']
            filename=f'chart-{len(results)+1}.png'
            (folder/filename).write_bytes(base64.b64decode(deep['charts'][0]['image_base64']))
            counts['sandbox_charts']+=1
            (folder/f'contract-{len(results)+1}.json').write_text(json.dumps(deep['charts'][0]['data_contract'],ensure_ascii=False,indent=2),encoding='utf-8')
        results.append({'case':case['name'],'passed':True})
    (folder/'results.json').write_text(json.dumps({'counts':counts,'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
    if render:
        (folder/'gallery.html').write_text('<!doctype html><meta charset="utf-8"><title>跨资料图表绑定验收</title><style>body{max-width:1100px;margin:40px auto;font:16px/1.6 sans-serif}img{max-width:100%}</style><h1>跨资料绑定与沙箱出图验收</h1><p>以下全部为虚构离线样本，不是研究结论。候选数据回放；校验、模板及Docker渲染使用真实项目流程。</p>'+''.join('<h2>'+html.escape(c['name'])+'</h2><img src="chart-'+str(i)+'.png">' for i,c in enumerate(cases,1)),encoding='utf-8')
    print(json.dumps({'counts':counts,'folder':str(folder)},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--render',action='store_true')
    asyncio.run(main(parser.parse_args().render))
