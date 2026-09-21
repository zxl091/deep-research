import copy
import importlib.util
from pathlib import Path
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

path = Path(__file__).resolve().parents[1] / 'service/deep_research_v2/chart_contract.py'
spec = importlib.util.spec_from_file_location('chart_contract', path)
charts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(charts)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.sources = {'https://example.test/demo': {'content': '虚构演示：2024年全年虚构公司营收40亿元；2025年全年虚构公司营收50亿元。'}}
        self.chart = {'title': '虚构演示营收', 'type': 'line', 'points': [dict(
            label=str(year), metric='营收', unit='亿元', scope='虚构公司', period=str(year),
            period_basis='全年', value_kind='actual', value=value,
            source_url='https://example.test/demo', quote=f'{year}年全年虚构公司营收{value}亿元') for year, value in [(2024,40),(2025,50)]]}

    def test_valid_grounded_series(self):
        accepted, errors = charts.validate_charts([self.chart], self.sources)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(errors, [])

    def test_metric_named_single_entity_line_is_normalized(self):
        for point in self.chart['points']:
            point['series'] = point['metric']
        accepted, errors = charts.validate_charts([self.chart], self.sources)
        self.assertFalse(errors)
        self.assertEqual({p['series'] for p in accepted[0]['points']}, {'虚构公司'})

    def test_metric_named_line_cannot_merge_different_entities(self):
        self.sources['https://example.test/demo']['content'] += '2025年全年另一公司营收50亿元。'
        self.chart['points'][1].update(entity='另一公司', quote='2025年全年另一公司营收50亿元')
        for point in self.chart['points']:
            point['series'] = point['metric']
        self.assertFalse(charts.validate_charts([self.chart], self.sources)[0])

    def test_recover_only_existing_valid_points_with_explicit_coverage(self):
        spec=copy.deepcopy(self.chart)
        spec['points'].append(dict(spec['points'][1], period='2026', quote='不存在的原句', value=55))
        recovered=charts.recover_partial_series([spec], self.sources)
        self.assertEqual(len(recovered),1)
        self.assertEqual([p['value'] for p in recovered[0]['points']],[40,50])
        self.assertNotIn('2026',recovered[0]['title'])
        self.assertIn('剔除 1',recovered[0]['coverage_note'])

    def test_partial_recovery_never_bypasses_series_dimensions_or_minimum(self):
        spec=copy.deepcopy(self.chart)
        spec['points'][1]['scope']='另一地区'
        spec['points'].append(dict(spec['points'][0],quote='不存在的原句'))
        self.assertEqual(charts.recover_partial_series([spec],self.sources),[])
        spec['points']=spec['points'][:1]+spec['points'][2:]
        self.assertEqual(charts.recover_partial_series([spec],self.sources),[])

    def test_typography_restores_actual_source_quote(self):
        source='2024 年，全年虚构公司营收 40 亿元。'
        self.assertEqual(charts.original_quote('2024年,全年虚构公司营收40亿元。',source),source)
        self.assertIsNone(charts.original_quote('2024年,全年虚构公司营收55亿元。',source))

    def test_invented_value_or_quote_rejected(self):
        for key, value in [('value',55), ('quote','2025年全年虚构公司营收55亿元'), ('source_url','https://unseen.test')]:
            with self.subTest(key=key):
                item=copy.deepcopy(self.chart);item['points'][1][key]=value
                self.assertFalse(charts.validate_charts([item], self.sources)[0])

    def test_mixed_units_metrics_or_periods_rejected(self):
        for key,value in [('unit','%'),('metric','销量'),('scope','全球'),('period_basis','上半年'),('value_kind','forecast')]:
            with self.subTest(key=key):
                item=copy.deepcopy(self.chart);item['points'][1][key]=value
                self.assertFalse(charts.validate_charts([item], self.sources)[0])

    def test_forecast_cannot_be_actual(self):
        item=copy.deepcopy(self.chart);item['points'][1]['quote']='预计2025年全年虚构公司营收50亿元'
        self.sources['https://example.test/demo']['content'] += '预计2025年全年虚构公司营收50亿元'
        self.assertFalse(charts.validate_charts([item],self.sources)[0])

    def test_cross_period_bar_rejected(self):
        self.chart['type']='bar'
        self.assertFalse(charts.validate_charts([self.chart],self.sources)[0])

    def test_code_in_title_is_only_data(self):
        self.chart['title']="'); raise RuntimeError('injected') #"
        code=charts.render_code(self.chart)
        compile(code,'<fixture>','exec')
        self.assertTrue(code.startswith('spec = json.loads('))


if __name__=='__main__': unittest.main(verbosity=2)
