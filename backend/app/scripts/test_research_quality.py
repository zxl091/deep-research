"""离线回归：重复资料、数字依据、审核路由、图谱方向。无需外部模型。"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.deep_research_v2.evidence_reuse import existing_fact, merge_facts, mark_extracted, new_sources, normalize_graph
from service.deep_research_v2.report_quality import check_report, normalize_review
from service.deep_research_v2.agents.critic import CriticMaster
from test_scout_recovery import scout, state, analysis


class EvidenceTests(unittest.TestCase):
    def test_same_source_merges_chapter_ownership_but_keeps_independent_sources(self):
        facts = [{'id': '1', 'source_url': 'a', 'content': '事实 甲', 'related_sections': ['s1']},
                 {'id': '2', 'source_url': 'a', 'content': '事实甲', 'related_sections': ['s2']},
                 {'id': '3', 'source_url': 'b', 'content': '事实甲', 'related_sections': ['s2']}]
        merged = merge_facts(facts)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['related_sections'], ['s1', 's2'])
        self.assertTrue(existing_fact({'facts': merged}, '事实甲', 'a', 's3'))
        self.assertIn('s3', merged[0]['related_sections'])

    def test_updated_body_and_different_chapter_still_get_extracted(self):
        data = {}
        rows = [{'url': 'a', 'summary': '甲'}]
        mark_extracted(data, 's1', rows)
        self.assertEqual(new_sources(data, 's1', rows * 2), [])
        self.assertEqual(new_sources(data, 's2', rows), rows)
        self.assertEqual(len(new_sources(data, 's1', [{'url': 'a', 'summary': '甲乙'}])), 1)

    def test_graph_only_repairs_unambiguous_direction_and_checks_endpoints(self):
        graph = {'nodes': [{'id': 'a', 'type': 'company'}, {'id': 'b', 'type': 'product'}],
                 'edges': [{'source': 'b', 'target': 'a', 'relation': '发布'},
                           {'source': 'a', 'target': 'missing', 'relation': '合作'}]}
        result = normalize_graph(graph)
        self.assertEqual(result['corrected_directions'], 1)
        self.assertEqual(len(result['issues']), 1)
        self.assertEqual(graph['edges'][0]['source'], 'a')
        self.assertEqual(normalize_graph(graph)['corrected_directions'], 0)


class NumericTests(unittest.TestCase):
    def setUp(self):
        self.sources = {'https://test/a': {'content': '2025年用户规模6.02亿人，普及率42.8%。企业收入10000万元。'},
                        'https://test/b': {'content': '资本开支107亿元，上期资本开支34亿元。预计2026年用户规模7亿人。'}}

    def test_actual_and_equivalent_units_pass(self):
        result = check_report('用户规模6.02亿人，普及率42.8%，收入1亿元。[来源](https://test/a)', self.sources)
        self.assertFalse(result['issues'])
        self.assertEqual(result['checked_quantities'], 3)

    def test_forecasts_are_not_exempt_from_grounding(self):
        result = check_report('预计用户7亿人、产业1.5万亿元、算力2000EFLOPS、投资增长30%。[来源](https://test/a)', self.sources)
        self.assertEqual(len(result['issues']), 4)
        self.assertFalse(check_report('预计2026年用户7亿人。[来源](https://test/b)', self.sources)['issues'])

    def test_wrong_citation_cannot_borrow_other_source_numbers(self):
        self.assertTrue(check_report('资本开支107亿元。[来源](https://test/a)', self.sources)['issues'])
        self.assertFalse(check_report('资本开支107亿元。[来源](https://test/b)', self.sources)['issues'])

    def test_explicit_reproducible_growth_formula(self):
        report = '资本开支107亿元、上期34亿元；同比增长(107-34)/34*100=214.7%。[来源](https://test/b)'
        self.assertFalse(check_report(report, self.sources)['issues'])
        self.assertTrue(check_report(report.replace('214.7%', '314.7%'), self.sources)['issues'])
        self.assertTrue(check_report('资本开支增长超3倍。[来源](https://test/b)', self.sources)['issues'])

    def test_forecast_cannot_reuse_an_actual_number_without_forecast_support(self):
        self.assertTrue(check_report('预计未来用户6.02亿人。[来源](https://test/a)', self.sources)['issues'])

    def test_policy_deadline_and_adjacent_actual_fact_are_not_false_alarms(self):
        sources = {'https://test/policy': {'content': '到2027年，应用普及率超70%。基金规模600亿元。'}}
        report = '政策提出2027年普及率超70%的目标，基金规模600亿元。[政策](https://test/policy)'
        self.assertFalse(check_report(report, sources)['issues'])

    def test_percentage_points_are_not_percent_or_counts(self):
        sources = {'a': {'content': '普及率增加25.2个百分点。'}}
        self.assertFalse(check_report('普及率增加25.2个百分点。', sources)['issues'])
        self.assertTrue(check_report('普及率增加25.2%。', sources)['issues'])

    def test_model_names_dates_and_qualitative_gaps_do_not_trigger(self):
        self.assertFalse(check_report('## 1. 2026年9月20日\nQwen3.5、V3.2。未检索到企业披露信息，未来增长存在不确定性。', {})['issues'])

    def test_hallucination_cannot_be_minor_and_pass_with_high_score(self):
        result = normalize_review({'overall_assessment': {'verdict': 'pass', 'quality_score': 8},
                                   'issues': [{'issue_type': 'hallucination', 'severity': 'minor'}]})
        self.assertEqual(result['overall_assessment']['verdict'], 'needs_revision')
        self.assertLess(result['overall_assessment']['quality_score'], 7)
        self.assertEqual(result['issues'][0]['severity'], 'major')

    def test_disclosed_source_limit_is_not_a_blocker(self):
        original = {'overall_assessment': {'verdict': 'pass', 'quality_score': 8},
                    'issues': [{'issue_type': 'missing_source', 'severity': 'minor'}]}
        self.assertEqual(normalize_review(original), original)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_sources_stop_recursive_extraction_but_changed_source_runs(self):
        value, data = scout(), state()
        section = data['outline'][0]
        rows = [{'url': 'https://test/source', 'summary': '事实'}]
        mark_extracted(data, section['id'], rows)
        value._search_for_state = AsyncMock(return_value=rows)
        value._analyze_deep_search_results = AsyncMock(return_value=analysis())
        await value._deep_search_query(data, section['id'], '追溯', 'source_tracing', [], 1, 2, 'first')
        value._analyze_deep_search_results.assert_not_awaited()
        value._search_for_state.return_value = [{'url': 'https://test/source', 'summary': '事实更新'}]
        await value._deep_search_query(data, section['id'], '追溯更新', 'source_tracing', [], 1, 2, 'second')
        value._analyze_deep_search_results.assert_awaited_once()

    async def test_critic_routes_bad_numeric_claim_to_revision(self):
        critic = object.__new__(CriticMaster)
        critic.name = 'critic'; critic.logger = Mock(); critic.add_message = Mock()
        critic._review_content = AsyncMock(return_value={'overall_assessment': {'verdict': 'pass', 'quality_score': 9}, 'issues': []})
        data = {'phase': 'reviewing', 'final_report': '无依据30%', 'critic_feedback': [], 'iteration': 0, 'max_iterations': 3,
                'report_validation': check_report('无依据30%', {})}
        await critic.process(data)
        self.assertEqual(data['phase'], 'revising')
        self.assertEqual(data['last_review']['overall_assessment']['verdict'], 'needs_revision')
        self.assertEqual(len(data['critic_feedback']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
