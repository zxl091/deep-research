"""报告质量修复的离线回归：正例与反例一起验证，不依赖模型自评分。"""
import asyncio
import copy
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.llm_config import LLMConfig
from service.assistant import llm
from service.assistant.full_research import export_report, source_context
from service.deep_research_v2.citations import normalize_citations
from service.deep_research_v2.report_quality import check_report
from service.deep_research_v2.chart_contract import validate_charts, recover_partial_series
from service.deep_research_v2.writing_pipeline import synthesize
from test_model_gateway import Stream, chunk
from test_writing_pipeline import writer, state


def share_case():
    content = '预计2029年市场规模增长。中国样本市场格局：2024下半年，前五家供应商合计76%。其中，甲公司以26%的份额排名第一，乙公司（19%）和丙公司（16%）紧随其后。'
    quote = content[content.index('其中'):]
    url = 'https://fixture.invalid/share'
    points = [dict(entity=n, label=n, metric='市场份额', unit='%', scope='中国样本市场',
        period='2024-H2', period_basis='下半年', value_kind='actual', value=v, source_url=url, quote=quote)
        for n, v in [('甲公司', 26), ('乙公司', 19), ('丙公司', 16)]]
    return {'type': 'bar', 'points': points, 'title': '部分企业份额'}, {url: {'content': content}}


class EvidenceFixes(unittest.TestCase):
    def test_review_sees_cited_source_before_recent_unrelated_facts(self):
        url = 'https://fixture.invalid/report'
        deep = {'final_report': f'甲公司市场份额42.2%。[报告]({url})',
                'facts': [{'source_url': 'https://fixture.invalid/recent'}]}
        sources = {url: {'content': '正文前言。'*500 + '甲公司以42.2%的市场份额领先。'},
                   'https://fixture.invalid/recent': {'content': '无关的近期补查'}}
        excerpt = source_context(deep, sources, limit=1)
        self.assertIn('甲公司以42.2%的市场份额领先。', excerpt)
        self.assertNotIn('无关的近期补查', excerpt)
        self.assertIn('原文中间部分省略', excerpt)
        self.assertLess(len(excerpt), 2200)

    def test_share_natural_language_and_half_year_context(self):
        spec, sources = share_case()
        charts, errors = validate_charts([spec], sources)
        self.assertFalse(errors)
        self.assertEqual(charts[0]['points'][0]['observation']['period']['start'], '2024-07-01')
        for mutation in ('swap_value', 'wrong_period', 'wrong_metric', 'remote_context'):
            bad, body = copy.deepcopy(spec), copy.deepcopy(sources)
            if mutation == 'swap_value': bad['points'][0]['value'] = 19
            if mutation == 'wrong_period': bad['points'][0]['period'] = '2024-H1'
            if mutation == 'wrong_metric': bad['points'][0]['metric'] = '收入占比'
            if mutation == 'remote_context':
                url = next(iter(body)); body[url]['content'] = body[url]['content'].replace('其中', '\n\n其中')
            with self.subTest(mutation=mutation): self.assertFalse(validate_charts([bad], body)[0])

    def test_inline_html_linebreaks_preserve_binding_but_not_paragraphs(self):
        spec, sources = share_case()
        url = next(iter(sources))
        for point in spec['points']:
            point['quote'] = point['quote'].replace('甲公司以26%的份额', '甲公司以\n26%\n的份额')
        sources[url]['content'] = sources[url]['content'].replace('甲公司以26%的份额', '甲公司以\n26%\n的份额')
        valid, errors = validate_charts([spec], sources)
        self.assertFalse(errors)
        self.assertEqual(valid[0]['points'][0]['value'], 26)
        broken = copy.deepcopy(spec)
        body = copy.deepcopy(sources)
        for point in broken['points']:
            point['quote'] = point['quote'].replace('甲公司以\n26%', '甲公司以\n\n26%')
        body[url]['content'] = body[url]['content'].replace('甲公司以\n26%', '甲公司以\n\n26%')
        self.assertFalse(validate_charts([broken], body)[0])

    def test_partial_share_becomes_bar_without_inventing_remainder(self):
        spec, sources = share_case(); spec['type'] = 'pie'
        self.assertFalse(validate_charts([spec], sources)[0])
        charts = recover_partial_series([spec], sources)
        self.assertEqual(charts[0]['type'], 'bar')
        self.assertEqual([p['value'] for p in charts[0]['points']], [26,19,16])
        self.assertIn('不足100%', charts[0]['coverage_note'])

    def test_equivalent_amount_and_half_not_unknown_number(self):
        source = {'https://test/a': {'content': '第一季度5700万中标项目金额。企业用户占比一半。'}}
        self.assertFalse(check_report('金额5700万元，企业用户占比50%。[资料](https://test/a)', source)['issues'])
        source['https://test/a']['content'] = '5700万用户，企业用户占比超过一半。'
        self.assertEqual(len(check_report('金额5700万元，企业用户占比50%。[资料](https://test/a)', source)['issues']), 2)

    def test_reference_mapping_never_consumes_following_chinese_prose(self):
        sources = {'https://test/a': '资料甲', 'https://test/b': '资料乙'}
        raw = '收入26亿元[https://test/a]。部署方式需要比较。\n另一句[资料](https://test/b)。'
        normalized = normalize_citations(raw, sources)
        self.assertIn('[资料甲](https://test/a)', normalized)
        evidence = [dict(url=u, id=f'E{i}', content='', title=n) for i,(u,n) in enumerate(sources.items(),1)]
        exported, _, cited = export_report(normalized, evidence)
        self.assertIn('收入26亿元[E1]。部署方式需要比较。', exported)
        self.assertEqual(cited, ['E1','E2'])
        self.assertNotIn('见证据列表', exported)
        self.assertEqual(normalize_citations(normalized, sources), normalized)
        unknown = normalize_citations('[https://unknown.test/a]。重要正文仍在。', sources)
        self.assertIn('来源未核验', unknown)
        self.assertIn('重要正文仍在', unknown)
        self.assertEqual(normalize_citations('[test/a]。', sources, numbered=True), '[test/a]。')
        self.assertEqual(normalize_citations('[example.com/a]。', {'https://example.com/a':'E1'}, numbered=True), '[E1]。')


class ModelAndWritingTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_roles_use_separate_models_and_trace(self):
        with patch.dict(os.environ, {'OPENAI_MODEL':'qwen3.7-plus-2026-05-26', 'RESEARCH_SEARCH_MODEL':'qwen3.7-flash'}):
            config = LLMConfig()
        seen = []
        async def create(**kwargs):
            await asyncio.sleep(0)
            seen.append(kwargs['model'])
            return Stream([chunk('ok','stop')])
        client = AsyncMock()
        client.__aenter__.return_value = NS(chat=NS(completions=NS(create=create)))
        usage = {}
        gateway = llm.ModelGateway(usage)
        async def call(role):
            token = llm.MODEL_CONTEXT.set({'role':role})
            try: return await gateway.complete('system','prompt')
            finally: llm.MODEL_CONTEXT.reset(token)
        with patch.object(llm,'get_config',return_value=config), patch.object(llm,'AsyncOpenAI',return_value=client), patch.object(llm.httpx,'AsyncClient'):
            await asyncio.gather(call('scout'),call('writer'),call('scout'),call('critic'))
        self.assertEqual(seen.count('qwen3.7-flash'),2)
        self.assertEqual(seen.count('qwen3.7-plus-2026-05-26'),2)
        for trace in usage['model_attempts']:
            self.assertEqual(trace['model'], 'qwen3.7-flash' if trace['role']=='scout' else config.default_model)

    async def test_comparison_table_assembled_with_all_chapters(self):
        value, data = writer(), state(); data['query'] = '对比甲乙的部署和客户'
        table = '|维度|甲|乙|\n|---|---|---|\n|部署|已知[资料](https://test/source)|本次未检索到|'
        value.call_llm.return_value = json.dumps(dict(executive_summary='差异', conclusions=['条件'], outlook='局限', comparison_table=table))
        await synthesize(value,data)
        self.assertIn(table,data['final_report'])
        for body in data['draft_sections'].values(): self.assertIn(body,data['final_report'])
        self.assertEqual(value.call_llm.call_args.kwargs['max_tokens'],6000)


if __name__ == '__main__': unittest.main(verbosity=2)
