"""数据先行绘图回归：均为离线虚构资料，不调用真实模型或写业务库。"""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.chart_inventory import verify_records, choose_charts
from service.deep_research_v2.chart_pipeline import grounded_charts
from service.deep_research_v2.evidence_records import YEAR_LABEL, explicit_enumeration_records
from service.deep_research_v2.chart_contract import validate_charts
from service.assistant.llm import ModelResponseTruncated, ModelQuotaExhausted


def sample(values=(('甲公司', 40), ('乙公司', 50)), *, period='2024', basis='全年', scope='企业样本', metric='营收', unit='亿元', url='https://fixture.test/data'):
    prefix = f'{period}年{basis if basis != YEAR_LABEL else ""}{scope}。'
    rows = [dict(entity=e, label=e, metric=metric, unit=unit, scope=scope, period=period,
        period_basis=basis, value_kind='actual', value=v, source_url=url,
        quote=prefix+f'{e}{metric}{v}{unit}。') for e,v in values]
    return rows, {url: {'content': '\n\n'.join(p['quote'] for p in rows), 'url': url}}


def enum_sample():
    quote = '在2025年大模型中标项目中，TOP5行业依次为金融、通信、能源、政务和教育科技，中标项目分别为98个、57个、31个、31个和27个。'
    rows, sources = sample(zip(['金融','通信','能源','政务','教育科技'], [98,57,31,31,27]),
        period='2025', basis=YEAR_LABEL, metric='中标项目', unit='个', scope='大模型中标项目')
    for p in rows:
        p['quote'] = quote
    sources[rows[0]['source_url']]['content'] = quote
    return rows, sources


class RecordTests(unittest.TestCase):
    def test_ordered_enumeration_binds_all_five_including_duplicate_values(self):
        rows, sources = enum_sample()
        records, bad = verify_records(rows, sources)
        self.assertFalse(bad, bad)
        self.assertEqual([r['point']['value'] for r in records], [98,57,31,31,27])
        specs, tables, errors = choose_charts(records, sources)
        self.assertEqual(specs[0]['type'], 'horizontal_bar')
        self.assertIn('未明确全年', specs[0]['display_note'])
        self.assertEqual(tables, [])
        self.assertFalse(errors)

    def test_swapped_entity_value_and_unequal_lists_are_rejected(self):
        rows, sources = enum_sample()
        rows[0]['value'] = 57
        self.assertEqual(len(verify_records(rows, sources)[0]), 4)
        rows, sources = enum_sample()
        for p in rows:
            p['quote'] = p['quote'].replace('、31个和27个', '和27个')
        sources[rows[0]['source_url']]['content'] = rows[0]['quote']
        self.assertEqual(verify_records(rows, sources)[0], [])

    def test_bare_year_is_not_promoted_to_full_year_or_trend(self):
        rows, sources = sample(basis=YEAR_LABEL)
        self.assertEqual(len(verify_records(rows, sources)[0]), 2)
        for p in rows:
            p['period_basis'] = '全年'
        self.assertEqual(validate_charts([{'type':'bar','points':rows}],sources)[0], [])
        records,bad=verify_records(rows,sources)
        self.assertFalse(bad)
        self.assertEqual(records[0]['point']['period_basis'],YEAR_LABEL)
        self.assertEqual(records[0]['point']['normalizations'][0]['proposed'],'全年')
        a, s1 = sample([('甲公司',40)], basis=YEAR_LABEL)
        b, s2 = sample([('甲公司',50)], period='2025', basis=YEAR_LABEL)
        s1[a[0]['source_url']]['content'] += '\n\n'+s2[b[0]['source_url']]['content']
        records, bad = verify_records(a+b, s1)
        self.assertFalse(bad)
        self.assertEqual(len(choose_charts(records,s1)[1]), 2)

    def test_half_year_cannot_be_downgraded_to_unknown_year(self):
        rows, sources = sample(basis='上半年')
        for p in rows:
            p['period_basis'] = YEAR_LABEL
        self.assertEqual(verify_records(rows, sources)[0], [])

    def test_partial_shares_remain_original_percentages_in_bar(self):
        rows, sources = sample([('甲公司',42.2),('乙公司',13.1),('丙公司',7.1)], metric='市场份额',unit='%',basis=YEAR_LABEL)
        records,bad = verify_records(rows,sources)
        self.assertFalse(bad)
        spec=choose_charts(records,sources)[0][0]
        self.assertEqual(spec['type'],'bar')
        self.assertEqual([p['value'] for p in spec['points']],[42.2,13.1,7.1])

    def test_scope_must_be_local_and_same_source_is_required(self):
        rows,sources=sample()
        rows[1]['scope']='其他市场'
        sources[rows[0]['source_url']]['content']+='\n\n其他市场背景'
        records,bad=verify_records(rows,sources)
        self.assertEqual(len(records),1)
        self.assertEqual(bad[0]['status'],'incompatible_scope')
        a,s1=sample([('甲公司',40)],url='https://fixture.test/a')
        b,s2=sample([('乙公司',50)],url='https://fixture.test/b')
        records,_=verify_records(a+b,s1|s2)
        self.assertEqual(choose_charts(records,s1|s2)[0],[])

    def test_conflicting_values_are_not_arbitrarily_selected(self):
        rows,sources=sample([('甲公司',40),('甲公司',41)])
        records,bad=verify_records(rows,sources)
        self.assertEqual(records,[])
        self.assertEqual({x['status'] for x in bad},{'conflicting_values'})

    def test_date_is_explicit_and_publication_date_cannot_replace_it(self):
        rows,sources=sample(metric='输入价格',unit='元')
        for p in rows:
            p.update(period='2025-05-01',period_basis='生效日期',quote=f"企业样本。2025年5月1日起执行，{p['entity']}输入价格{p['value']}元。")
        sources[rows[0]['source_url']]['content']='\n\n'.join(p['quote'] for p in rows)
        records,bad=verify_records(rows,sources)
        self.assertFalse(bad,bad)
        self.assertEqual(choose_charts(records,sources)[0][0]['type'],'bar')
        for p in rows:
            p['quote']=p['quote'].replace('起执行','发布文章')
        sources[rows[0]['source_url']]['content']='\n\n'.join(p['quote'] for p in rows)
        self.assertEqual(verify_records(rows,sources)[0],[])

    def test_mixed_periods_and_kinds_never_form_one_chart(self):
        a,s1=sample([('甲公司',40)])
        b,s2=sample([('甲公司',50)],basis='上半年')
        s1[a[0]['source_url']]['content']+='\n\n'+s2[b[0]['source_url']]['content']
        records,_=verify_records(a+b,s1)
        self.assertEqual(choose_charts(records,s1)[0],[])
        rows,sources=sample()
        rows[1]['value_kind']='forecast'
        records,_=verify_records(rows,sources)
        self.assertEqual(choose_charts(records,sources)[0],[])

    def test_deterministic_enumeration_recovers_omitted_list_from_original(self):
        rows,sources=enum_sample()
        extracted=list(explicit_enumeration_records(sources))
        self.assertEqual(len(extracted),5)
        records,bad=verify_records(extracted,sources)
        self.assertFalse(bad,bad)
        self.assertEqual(len(choose_charts(records,sources)[0]),1)
        self.assertEqual([r['point']['value'] for r in records],[98,57,31,31,27])

    def test_annual_price_claim_cannot_be_downgraded_and_compared(self):
        rows,sources=sample(metric='输入价格',unit='元',basis=YEAR_LABEL)
        for p in rows:p['period_basis']='全年'
        records,bad=verify_records(rows,sources)
        self.assertFalse(records)
        self.assertTrue(all(r['status']=='period_ambiguous' for r in bad))

    def test_first_eleven_months_is_cumulative_not_annual_or_unknown(self):
        _,sources=enum_sample()
        url=next(iter(sources))
        sources[url]['content']=sources[url]['content'].replace('2025年','2025年前11个月的')
        rows=list(explicit_enumeration_records(sources))
        records,bad=verify_records(rows,sources)
        self.assertFalse(bad,bad)
        self.assertEqual(len(records),5)
        p=records[0]['point']
        self.assertEqual(p['period'],'2025-11')
        self.assertEqual(p['period_basis'],'累计')
        self.assertEqual(p['observation']['period']['start'],'2025-01-01')
        self.assertEqual(p['observation']['period']['end'],'2025-11-30')

    def test_reprints_do_not_generate_duplicate_charts(self):
        rows,sources=enum_sample()
        url2='https://fixture.test/reprint'
        other=[dict(p,source_url=url2) for p in rows]
        sources[url2]=copy.deepcopy(next(iter(sources.values())))
        records,bad=verify_records(rows+other,sources)
        self.assertFalse(bad)
        specs,tables,_=choose_charts(records,sources)
        self.assertEqual(len(specs),1)
        self.assertEqual(len(specs[0]['corroborating_evidence_ids']),5)
        self.assertEqual(tables,[])

    def test_unrelated_future_target_cannot_relabel_historical_enumeration(self):
        _,sources=enum_sample()
        url=next(iter(sources))
        sources[url]['content']='2026年收入目标增长200%。'+sources[url]['content']
        rows=list(explicit_enumeration_records(sources))
        self.assertTrue(all(p['value_kind']=='actual' for p in rows))
        records,bad=verify_records(rows,sources)
        self.assertFalse(bad,bad)
        self.assertEqual(len(records),5)
        rows[0]['value_kind']='target'
        self.assertEqual(len(verify_records(rows,sources)[0]),4)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows,self.sources=sample()
        self.deep={'query':'比较企业样本','outline':[],'facts':[],'charts':[]}
        self.model=SimpleNamespace(complete=AsyncMock(return_value={'records':self.rows}))
        self.execute=AsyncMock(return_value={'success':True,'charts':['fixture-image']})

    async def run_flow(self,**kwargs):
        await grounded_charts(self.deep,self.sources,self.model,self.execute,**kwargs)

    async def test_data_precedes_planning_and_cached_resume_does_not_call_model_or_sandbox_again(self):
        self.deep['chart_plan']=[{'title':'应被忽略的十点折线图','type':'line','desired_points':10}]
        await self.run_flow()
        self.assertEqual(self.deep['charts'][0]['chart_type'],'bar')
        self.assertEqual(len(self.deep['chart_evidence_records']['records']),2)
        self.assertEqual(self.model.complete.await_count,1)
        await self.run_flow()
        self.assertEqual(self.model.complete.await_count,1)
        self.assertEqual(self.execute.await_count,1)

    async def test_single_point_is_retained_as_table_not_execution_error(self):
        self.model.complete.return_value={'records':self.rows[:1]}
        await self.run_flow()
        self.assertEqual(self.deep['charts'],[])
        self.assertEqual(self.deep['chart_validation']['table_only'],1)
        self.assertEqual(self.deep['errors'],[])
        self.execute.assert_not_awaited()

    async def test_parse_failure_does_not_trigger_blind_search(self):
        self.rows[0]['quote']='不存在的原文'
        supplement=AsyncMock()
        await self.run_flow(supplement=supplement)
        supplement.assert_not_awaited()
        self.assertIn('source_missing',[i['status'] for i in self.deep['chart_validation']['items']])

    async def test_explicit_missing_source_gets_one_targeted_search(self):
        self.model.complete.side_effect=[{'records':[], 'missing_data':[{'reason':'source_missing','field':'营收','query':'企业样本营收'}]}, {'records':self.rows}]
        async def add_sources(*args):
            self.sources[self.rows[0]['source_url']]['content'] += '补充的原文说明'
            return list(self.sources)
        supplement=AsyncMock(side_effect=add_sources)
        await self.run_flow(supplement=supplement)
        supplement.assert_awaited_once_with('企业样本营收',None)
        self.assertEqual(len(self.deep['charts']),1)

    async def test_render_failure_does_not_block_other_verified_series(self):
        rows,sources=enum_sample()
        # 使用不同 URL，避免两份原文彼此覆盖。
        for p in rows:p['source_url']='https://fixture.test/enumeration'
        self.sources['https://fixture.test/enumeration']=dict(next(iter(sources.values())),url='https://fixture.test/enumeration')
        self.model.complete.return_value={'records':self.rows+rows}
        self.execute.side_effect=[RuntimeError('sandbox unavailable'),{'success':True,'charts':['fixture']}]
        await self.run_flow()
        self.assertEqual(len(self.deep['charts']),1)
        self.assertIn('render_failed',[i['status'] for i in self.deep['chart_validation']['items']])

    async def test_failure_does_not_erase_previous_charts_and_is_not_insufficient_data(self):
        await self.run_flow()
        chart=copy.deepcopy(self.deep['charts'][0])
        self.sources[self.rows[0]['source_url']]['content']+='新增来源'
        self.model.complete.side_effect=ModelResponseTruncated('length')
        await self.run_flow()
        self.assertEqual(self.deep['charts'][0]['id'],chart['id'])
        self.assertEqual(self.deep['charts'][0]['image_base64'],chart['image_base64'])
        self.assertIn('extraction_failed',[i['status'] for i in self.deep['chart_validation']['items']])

    async def test_cancellation_and_quota_propagate_without_batch_retries(self):
        for error in (asyncio.CancelledError(),ModelQuotaExhausted('quota')):
            model=SimpleNamespace(complete=AsyncMock(side_effect=error))
            with self.assertRaises(type(error)):
                await grounded_charts({'query':'test','charts':[]},self.sources,model,self.execute)
            self.assertEqual(model.complete.await_count,1)

    async def test_changed_source_invalidates_model_cache(self):
        await self.run_flow()
        self.sources[self.rows[0]['source_url']]['content']+='新证据'
        await self.run_flow()
        self.assertEqual(self.model.complete.await_count,2)

    async def test_batch_truncation_preserves_other_batch_and_success_checkpoint(self):
        self.sources['https://fixture.test/extra']={'url':'https://fixture.test/extra','content':'背景文字'*1000}
        self.sources[self.rows[0]['source_url']]['content']+='\n\n'+'测试说明'*1000
        self.model.complete.side_effect=[{'records':self.rows},ModelResponseTruncated('length')]
        await self.run_flow()
        self.assertEqual(len(self.deep['charts']),1)
        self.assertEqual(self.model.complete.await_count,2)
        self.assertEqual(len(self.deep['chart_model_checkpoints']),1)
        self.assertIn('extraction_failed',[i['status'] for i in self.deep['chart_validation']['items']])

    async def test_invalid_model_records_shape_is_visible(self):
        self.model.complete.return_value={'records':None}
        await self.run_flow()
        self.assertEqual(self.deep['chart_validation']['items'][0]['status'],'extraction_failed')
        self.execute.assert_not_awaited()

    async def test_offline_replay_never_calls_model_search_or_fetch(self):
        self.deep['chart_evidence_records']={'candidate_records':self.rows}
        search,fetch=AsyncMock(),AsyncMock()
        await self.run_flow(replay_saved=True,supplement=search,fetch_source=fetch)
        self.model.complete.assert_not_awaited()
        search.assert_not_awaited()
        fetch.assert_not_awaited()
        self.assertEqual(len(self.deep['charts']),1)


if __name__=='__main__':unittest.main(verbosity=2)
