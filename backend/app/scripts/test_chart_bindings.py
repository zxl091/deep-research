"""图表公开校验入口的语义绑定测试；所有资料均为虚构离线样本。"""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.chart_contract import validate_charts
from service.deep_research_v2.chart_evidence import structured_html


def table_case():
    body = '''<h2>2024年上半年企业合并收入（实际值）</h2><table>
      <tr><th>公司</th><th>营业收入（亿元）</th></tr>
      <tr><td>甲公司</td><td>40</td></tr><tr><td>乙公司</td><td>50</td></tr></table>'''
    content, tables = structured_html(body)
    chart, _ = text_case()
    for row, point in enumerate(chart['points'], 1):
        point.update(quote=f"{point['entity']} | {point['value']}", context_quote='2024年上半年企业合并收入（实际值）',
                     table_ref={'id': 'table_0', 'row': row, 'column': 1})
    return chart, {chart['points'][0]['source_url']: dict(content=content, tables=tables)}


def text_case():
    quote = '2024年上半年，甲公司营业收入40亿元，乙公司营业收入50亿元。'
    url = 'https://fixture.invalid/revenue'
    points = [dict(label=entity, entity=entity, metric='营业收入', value=value, unit='亿元',
                   period='2024-H1', period_basis='上半年', scope='企业样本', value_kind='actual',
                   source_url=url, quote=quote) for entity, value in [('甲公司', 40), ('乙公司', 50)]]
    return dict(type='bar', title='虚构企业收入', points=points), {url: dict(content=quote)}


class BindingTests(unittest.TestCase):
    def test_half_year_series_preserves_dates_and_legend_identity(self):
        points=[]
        for half,value in [(1,40),(2,50)]:
            points.append(dict(entity='样本公司',series='样本公司',label=f'2024-H{half}',period=f'2024-H{half}',
                period_basis='半年度',metric='营业收入',unit='亿元',value=value,value_kind='actual',scope='样本公司',
                source_url='https://fixture.invalid',quote=f"2024年{'上' if half==1 else '下'}半年样本公司营业收入{value}亿元"))
        chart=dict(type='line',points=points)
        sources={'https://fixture.invalid':{'content':'；'.join(p['quote'] for p in points)}}
        accepted,errors=validate_charts([chart],sources)
        self.assertFalse(errors)
        self.assertEqual(accepted[0]['points'][1]['observation']['period']['start'],'2024-07-01')
        for p in chart['points']:p['series']='其他公司'
        self.assertFalse(validate_charts([chart],sources)[0])

    def test_text_cannot_borrow_another_values_unit_or_remote_year(self):
        chart, sources = text_case()
        url=chart['points'][0]['source_url']
        sources[url]['content']=sources[url]['content'].replace('40亿元','40万元')
        for p in chart['points']:p['quote']=sources[url]['content']
        self.assertFalse(validate_charts([chart],sources)[0])
        chart,sources=text_case()
        body='2023年上半年企业收入如下。甲公司营业收入40亿元，乙公司营业收入50亿元。\n\n2024年上半年行业背景。'
        sources[url]['content']=body
        for p in chart['points']:
            p.update(quote=f"{p['entity']}营业收入{p['value']}亿元",context_quote='2024年上半年行业背景。')
        self.assertFalse(validate_charts([chart],sources)[0])

    def test_long_table_cannot_treat_growth_column_as_revenue(self):
        body='<h2>2024年全年统计</h2><table><tr><th>对象</th><th>指标</th><th>数值</th><th>同比</th><th>单位</th></tr><tr><td>甲公司</td><td>营业收入</td><td>40</td><td>5</td><td>亿元</td></tr><tr><td>乙公司</td><td>营业收入</td><td>50</td><td>6</td><td>亿元</td></tr></table>'
        content,tables=structured_html(body)
        chart,_=text_case();url=chart['points'][0]['source_url']
        for i,p in enumerate(chart['points'],1):
            p.update(value=4+i,period='2024',period_basis='全年',quote=' | '.join(tables[0]['rows'][i]),table_ref={'id':'table_0','row':i,'column':3})
        self.assertFalse(validate_charts([chart],{url:dict(content=content,tables=tables)})[0])

    def test_wrong_display_label_cannot_hide_behind_correct_entity(self):
        chart, sources = table_case()
        chart['points'][0]['label'] = '其他公司'
        self.assertFalse(validate_charts([chart], sources)[0])

    def test_unknown_scope_is_not_filled_with_the_model_scope(self):
        chart, sources = table_case()
        accepted, _ = validate_charts([chart], sources)
        record = accepted[0]['points'][0]['observation']
        self.assertIsNone(record['statistical_scope'])
        self.assertIn('statistical_scope', record['unknown_fields'])

    def test_plain_year_is_not_assumed_to_be_a_full_year(self):
        chart, sources = text_case()
        url = chart['points'][0]['source_url']
        sources[url]['content'] = sources[url]['content'].replace('上半年', '')
        for p in chart['points']:
            p.update(period='2024',period_basis='全年',quote=sources[url]['content'])
        accepted, errors = validate_charts([chart], sources)
        self.assertFalse(accepted)
        self.assertIn('period', errors[0])

    def test_text_whitespace_and_punctuation_do_not_change_binding(self):
        chart, sources = text_case()
        url = chart['points'][0]['source_url']
        sources[url]['content'] = sources[url]['content'].replace('40亿元','40 亿元').replace('，', ',')
        accepted, errors = validate_charts([chart], sources)
        self.assertFalse(errors)
        self.assertEqual([p['value'] for p in accepted[0]['points']], [40,50])

    def test_quarter_and_cumulative_are_not_interchangeable(self):
        chart, sources = text_case()
        url = chart['points'][0]['source_url']
        sources[url]['content'] = sources[url]['content'].replace('上半年', '前三季度')
        for p in chart['points']:
            p.update(period='2024-Q3',period_basis='季度',quote=sources[url]['content'])
        self.assertFalse(validate_charts([chart], sources)[0])
        for p in chart['points']:p.update(period='2024年前三季度',period_basis='累计')
        accepted,errors=validate_charts([chart], sources)
        self.assertFalse(errors)
        self.assertEqual(accepted[0]['points'][0]['observation']['period']['start'], '2024-01-01')
        self.assertEqual(accepted[0]['points'][0]['observation']['period']['end'], '2024-09-30')

    def test_table_reordering_whitespace_and_missing_fields(self):
        chart, sources = table_case()
        url = chart['points'][0]['source_url']
        reordered = copy.deepcopy(sources)
        reordered[url]['tables'][0]['rows'][1:] = list(reversed(reordered[url]['tables'][0]['rows'][1:]))
        self.assertFalse(validate_charts([chart], reordered)[0], '旧行号不得指向另一家公司')
        for p in chart['points']: p['table_ref']['row'] = 3 - p['table_ref']['row']
        accepted, errors = validate_charts([chart], reordered)
        self.assertFalse(errors)
        self.assertEqual([p['value'] for p in accepted[0]['points']], [40, 50])
        chart, sources = table_case()
        sources[url]['content'] = sources[url]['content'].replace(' | ', '  |  ')
        self.assertTrue(validate_charts([chart], sources)[0], '排版空白不能改变绑定')
        for field in ('unit', 'period', 'merged_header'):
            with self.subTest(field=field):
                bad = copy.deepcopy(sources)
                table = bad[url]['tables'][0]
                if field == 'unit': table['rows'][0][1] = '营业收入'
                if field == 'period': table['context'] = '企业合并收入（实际值）'
                if field == 'merged_header': table['simple'] = False
                accepted, errors = validate_charts([chart], bad)
                self.assertFalse(accepted)
                self.assertTrue(any(name in errors[0] for name in ('unit', 'period', 'table_ref')))

    def test_monthly_sales_and_forecast_scale_have_independent_periods_and_units(self):
        cases = [
            ('2024年5月', '2024-05', '月度', '销量', '辆', '实际值', 'actual', '当月', [120, 80]),
            ('2027年全年', '2027', '全年', '市场规模', '亿元', '预测值', 'forecast', '期间合计', [250, 310]),
        ]
        for date, period, basis, metric, unit, kind_text, kind, aggregation, values in cases:
            with self.subTest(metric=metric):
                html = '<table><tr><th>对象</th><th>指标</th><th>期间</th><th>数值</th><th>单位</th><th>数据属性</th><th>统计方式</th><th>口径</th></tr>'
                html += ''.join(f'<tr><td>{entity}</td><td>{metric}</td><td>{date}</td><td>{value}</td><td>{unit}</td><td>{kind_text}</td><td>{aggregation}</td><td>样本统计</td></tr>' for entity, value in zip(['样本甲', '样本乙'], values)) + '</table>'
                content, tables = structured_html(html)
                points = [dict(entity=entity, label=entity, metric=metric, period=period, period_basis=basis,
                    value=value, unit=unit, value_kind=kind, scope='行业样本', source_url='https://fixture.invalid',
                    quote=' | '.join(tables[0]['rows'][i]), table_ref={'id':'table_0','row':i,'column':3})
                    for i,(entity,value) in enumerate(zip(['样本甲','样本乙'],values),1)]
                chart = dict(type='bar', points=points)
                sources = {'https://fixture.invalid':dict(content=content,tables=tables)}
                accepted, errors = validate_charts([chart], sources)
                self.assertFalse(errors)
                self.assertEqual(accepted[0]['points'][0]['observation']['aggregation'], aggregation)
                bad = copy.deepcopy(chart)
                bad['points'][0]['unit'] = '亿元' if unit == '辆' else '万元'
                self.assertFalse(validate_charts([bad], sources)[0])
                bad = copy.deepcopy(chart)
                for point in bad['points']: point['value_kind'] = 'forecast' if kind == 'actual' else 'target'
                self.assertFalse(validate_charts([bad], sources)[0], '明确的数据属性不能被模型改写')
                if kind == 'forecast':
                    bad = copy.deepcopy(chart)
                    for point in bad['points']: point['value_kind'] = 'actual'
                    self.assertFalse(validate_charts([bad], sources)[0])

    def test_table_binds_identity_to_cell_instead_of_trusting_model_fields(self):
        chart, sources = table_case()
        accepted, errors = validate_charts([chart], sources)
        self.assertFalse(errors)
        record = accepted[0]['points'][0]['observation']
        self.assertEqual(record['entity'], '甲公司')
        self.assertEqual(record['period']['end'], '2024-06-30')
        self.assertEqual(record['anchor']['row'], 1)
        bad = copy.deepcopy(chart)
        bad['points'][0].update(label='乙公司', entity='乙公司')
        self.assertFalse(validate_charts([bad], sources)[0])

    def test_revenue_keeps_entity_and_period_binding(self):
        chart, sources = text_case()
        accepted, errors = validate_charts([chart], sources)
        self.assertFalse(errors)
        self.assertEqual(len(accepted), 1)
        for mutate in ('swap_companies', 'half_to_full', 'H1_to_H2'):
            with self.subTest(mutate=mutate):
                bad = copy.deepcopy(chart)
                if mutate == 'swap_companies':
                    bad['points'][0]['value'], bad['points'][1]['value'] = 50, 40
                else:
                    for point in bad['points']:
                        point.update(period='2024' if mutate == 'half_to_full' else '2024-H2',
                                     period_basis='全年' if mutate == 'half_to_full' else '下半年')
                accepted, errors = validate_charts([bad], sources)
                self.assertFalse(accepted, '错误的对象或期间绑定不能出图')
                self.assertTrue(errors)


if __name__ == '__main__': unittest.main(verbosity=2)
