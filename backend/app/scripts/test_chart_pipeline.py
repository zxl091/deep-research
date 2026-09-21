import asyncio
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chart_test_fixtures import chart_fixtures
from service.deep_research_v2.chart_contract import validate_charts, recover_partial_series, render_code
from service.deep_research_v2.chart_shapes import period_position, CHART_TYPES
from service.deep_research_v2.chart_pipeline import grounded_charts, select_sources


class ExtendedContracts(unittest.TestCase):
    def setUp(self): self.specs,self.sources=chart_fixtures()
    def spec(self,kind):return copy.deepcopy(next(s for s in self.specs if s['type']==kind))

    def test_all_supported_types_have_grounded_contract_and_compilable_template(self):
        self.assertEqual({s['type'] for s in self.specs},CHART_TYPES)
        for spec in self.specs:
            with self.subTest(kind=spec['type']):
                valid,errors=validate_charts([spec],self.sources)
                self.assertFalse(errors,errors);compile(render_code(valid[0]),'<chart>','exec')

    def test_more_than_twelve_periods_are_allowed_without_interpolation(self):
        spec=self.spec('line'); base=spec['points'][0];rows=[]
        for i in range(24):
            p=dict(base,period=str(2000+i),label=str(2000+i),value=i+10,quote=f'{2000+i}年全年样本公司营收{i+10}亿元')
            rows.append(p)
        self.sources[base['source_url']]['content']='；'.join(p['quote'] for p in rows)
        spec['points']=rows
        self.assertEqual(len(validate_charts([spec],self.sources)[0][0]['points']),24)
        self.assertEqual(period_position('2025')-period_position('2021'),4)
        self.assertAlmostEqual(period_position('2024-Q3'),2024.5)

    def test_two_points_are_marked_as_discrete_observations(self):
        spec=self.spec('line');spec['points']=spec['points'][:2]
        valid,_=validate_charts([spec],self.sources)
        self.assertIn('不连线',valid[0]['display_note'])

    def test_no_partial_pie_or_zero_filled_matrix(self):
        for kind in ['pie','donut','grouped_bar','stacked_bar','radar','scatter','heatmap']:
            with self.subTest(kind=kind):
                spec=self.spec(kind);spec['points'].pop()
                self.assertFalse(validate_charts([spec],self.sources)[0])
                recovered = recover_partial_series([spec],self.sources)
                if kind in ('pie', 'donut'):
                    self.assertEqual(recovered[0]['type'], 'bar')
                    self.assertEqual([p['value'] for p in recovered[0]['points']], [p['value'] for p in spec['points']])
                else:
                    self.assertEqual(recovered,[])

    def test_duplicate_matrix_coordinates_and_mixed_radar_metrics_rejected(self):
        spec=self.spec('heatmap');spec['points'][-1]=copy.deepcopy(spec['points'][0])
        self.assertFalse(validate_charts([spec],self.sources)[0])
        spec=self.spec('radar');spec['points'][0]['metric']='未校准主观分数'
        self.assertFalse(validate_charts([spec],self.sources)[0])

    def test_table_header_must_be_an_actual_quote_from_same_source(self):
        spec=self.spec('bar')
        for p in spec['points']:
            p['quote']=f"企业{p['label']}营收记录：{p['value']}"
            p['context_quote']='2024年全年企业营收表（单位：亿元）'
            self.sources[p['source_url']]['content']=p['context_quote']+'\n'+p['quote']
        self.assertEqual(len(validate_charts([spec],self.sources)[0]),1)
        spec['points'][0]['context_quote']='2025年企业全年营收表（单位：亿元）'
        self.assertFalse(validate_charts([spec],self.sources)[0])

    def test_explicit_unit_conversion_is_checked_not_guessed(self):
        spec=self.spec('bar')
        for p in spec['points']:
            p.update(original_value=p['value'],original_unit='亿元',unit='万元',value=p['value']*10000)
        valid,errors=validate_charts([spec],self.sources)
        self.assertFalse(errors);self.assertIn('conversion_note',valid[0]['points'][0])
        spec['points'][0]['value']+=1
        self.assertFalse(validate_charts([spec],self.sources)[0])

    def test_bar_can_recover_valid_categories_but_must_disclose_omission(self):
        spec=self.spec('bar');spec['points'][-1]['quote']='原文不存在的虚构句子'
        valid=recover_partial_series([spec],self.sources)
        self.assertEqual(len(valid[0]['points']),2);self.assertIn('剔除 1',valid[0]['coverage_note'])

    def test_source_selection_can_reach_figures_after_long_introduction(self):
        sources={'test':{'content':'背景说明。'*2000+'2024年全年营业收入50亿元，2025年全年营业收入60亿元。','url':'test'}}
        chosen=select_sources({'facts':[]},sources,{'data_question':'营业收入'})
        self.assertIn('营业收入60亿元',chosen['test']['content'])
        self.assertGreater(len(chosen['test']['content']),0)

    def test_mixed_actual_and_forecast_recovery_keeps_attributes_separate(self):
        spec=self.spec('line')
        spec['points'][2]['value_kind']='forecast'
        groups=recover_partial_series([spec],self.sources)
        self.assertEqual(len(groups),1)
        self.assertEqual(len(groups[0]['points']),2)
        self.assertTrue(all(p['value_kind']=='actual' for p in groups[0]['points']))
        self.assertIn('实际值',groups[0]['title'])
        self.assertIn('统计口径不同',groups[0]['coverage_note'])

    def test_target_bounds_are_not_presented_as_exact_values(self):
        spec=self.spec('line')
        for p in spec['points']:
            p['value_kind']='target';p['quote']=f"到{p['period']}年全年，样本公司营收达到{p['value']}亿元以上"
            self.sources[p['source_url']]['content']=p['quote']
        valid,_=validate_charts([spec],self.sources)
        self.assertTrue(all(p['qualifier']=='at_least' for p in valid[0]['points']))

    def test_prefix_bounds_and_approximate_values_are_preserved(self):
        spec=self.spec('bar')
        forms=[('不低于','at_least'),('约为','approximate'),('超过','more_than')]
        for p,(prefix,kind) in zip(spec['points'],forms):
            p['quote']=f"{p['period']}年全年{p['entity']}营收{prefix}{p['value']}亿元"
            self.sources[p['source_url']]['content']=p['quote']
            p['qualifier']='at_most'  # 错误模型标签必须由原文纠正。
        valid,errors=validate_charts([spec],self.sources)
        self.assertFalse(errors)
        self.assertEqual([p['qualifier'] for p in valid[0]['points']],[kind for _,kind in forms])


if __name__=='__main__':unittest.main(verbosity=2)
