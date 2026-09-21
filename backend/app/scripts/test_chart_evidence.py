import asyncio
import copy
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.chart_contract import validate_charts, render_code
from service.deep_research_v2.chart_evidence import structured_html
from service.deep_research_v2.agents.wizard import CodeWizard
from service.deep_research_v2.chart_pipeline import grounded_charts


def point(year, value, quote):
    return dict(label=str(year), period=str(year), value=value, quote=quote, source_url='https://example.test',
                metric='营业收入', unit='亿元', scope='样本公司', period_basis='全年', value_kind='actual')


class EvidenceTests(unittest.TestCase):
    def test_nearby_year_is_recovered_but_conflicting_years_are_not(self):
        rows=[point(2024, 40, '甲公司营业收入40亿元'),point(2024, 50, '乙公司营业收入50亿元')]
        rows[0]['label']=rows[0]['entity']='甲公司';rows[1]['label']=rows[1]['entity']='乙公司'
        spec=dict(type='bar', points=rows)
        source={'https://example.test': {'content':'两家公司2024年全年财务业绩如下。甲公司营业收入40亿元，乙公司营业收入50亿元。'}}
        valid,errors=validate_charts([spec],source)
        self.assertFalse(errors); self.assertIn('2024',valid[0]['points'][0]['context_quote'])
        source['https://example.test']['content']='2023年和2024年财务业绩如下。甲公司营业收入40亿元，乙公司营业收入50亿元。'
        self.assertFalse(validate_charts([spec],source)[0])

    def test_planning_period_bar_and_approximation(self):
        a=point('2011-2015',4005,'样本公司十二五期间营业收入目标4005亿元')
        b=point('2021-2025',6700,'样本公司十四五期间营业收入目标约6700亿元')
        for p,label in [(a,'十二五'),(b,'十四五')]:p.update(label=label,entity='样本公司',period_basis='五年规划期',value_kind='target')
        sources={'https://example.test':{'content':a['quote']+'；'+b['quote']}}
        valid,errors=validate_charts([dict(type='bar',points=[a,b])],sources)
        self.assertFalse(errors);self.assertEqual(valid[0]['points'][1]['qualifier'],'approximate')
        single,_=validate_charts([dict(type='grouped_bar',points=[a,b])],sources)
        self.assertEqual(single[0]['type'],'bar')
        a['period']='2012-2016'
        self.assertFalse(validate_charts([dict(type='bar',points=[a,b])],sources)[0])

    def test_glued_numbers_are_not_guessed(self):
        p=point(2024,89.56,'2024年节能服务282532.4589.56占比%')
        p['unit']='%'
        valid,errors=validate_charts([dict(type='bar',points=[p,p])],{'https://example.test':{'content':p['quote']}})
        self.assertFalse(valid);self.assertIn('粘连',errors[0])

    def test_html_preserves_cells_and_relative_year_column(self):
        html='<h2>2023 年度 单位：亿元</h2><table><tr><th>对象</th><th>指标</th><th>本报告期</th><th>上年同期</th></tr><tr><td>样本公司</td><td>营业收入</td><td>50</td><td>40</td></tr></table>'
        content,tables=structured_html(html)
        self.assertIn('样本公司 | 营业收入 | 50 | 40',content)
        p=point(2022,40,'样本公司 | 营业收入 | 50 | 40')
        p.update(entity='样本公司',context_quote='2023 年度 单位：亿元',table_ref=dict(id='table_0',row=1,column=3))
        q=point(2023,50,p['quote']);q.update(entity='样本公司',context_quote=p['context_quote'],table_ref=dict(id='table_0',row=1,column=2))
        sources={'https://example.test':dict(content=content,tables=tables)}
        valid,errors=validate_charts([dict(type='line',points=[p,q])],sources)
        self.assertFalse(errors);self.assertIn('上年同期',valid[0]['points'][0]['period_note'])
        p['table_ref']['column']=2
        self.assertFalse(validate_charts([dict(type='line',points=[p,q])],sources)[0])


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_code_reaches_sandbox_without_escape_corruption(self):
        code=render_code(dict(type='line',title="引号'与换行",points=[point(2023,40,'2023年40亿元\n原文'),point(2024,50,'2024年50亿元')]))
        w=object.__new__(CodeWizard);w.logger=logging.getLogger('fixture');w._save_debug_log=lambda *a:None
        with patch('service.deep_research_v2.sandbox.execute_isolated',new_callable=AsyncMock) as sandbox:
            sandbox.return_value={'success':True,'charts':['fixture']}
            await w._execute_code(code)
            self.assertEqual(sandbox.call_args.args[0],code)
            compile(sandbox.call_args.args[0],'<test>','exec')

    async def test_ledger_parse_failure_and_source_fetch_failure_are_distinct(self):
        p=point(2024,89.56,'2024年节能服务282532.4589.56占比%');p['unit']='%'
        spec=dict(type='bar',points=[p,p])
        deep=dict(query='test',outline=[],charts=[],facts=[],chart_plan=[dict(id='a',title='test',desired_points=2,type='bar')],
                  chart_validation={'items':[dict(plan_id='a',attempts=[dict(proposed=[spec])])]})
        model=type('M',(),{})();model.complete=AsyncMock(return_value={'records':spec['points']})
        fetch=AsyncMock(side_effect=RuntimeError('denied'));execute=AsyncMock()
        await grounded_charts(deep,{'https://example.test':{'content':p['quote']}},model,execute,fetch_source=fetch,reuse_proposals=True)
        self.assertEqual(model.complete.await_count,1);execute.assert_not_awaited()
        item=deep['chart_validation']['items'][0]
        self.assertEqual(item['status'],'parse_failed');self.assertEqual(deep['chart_validation']['fetches'][0]['status'],'source_fetch_failed')


if __name__=='__main__':unittest.main(verbosity=2)
