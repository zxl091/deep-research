"""完整研究编排回归：用专家替身验证调度，数据库模型使用真实测试环境。"""
import asyncio
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from dotenv import load_dotenv
APP = Path(__file__).resolve().parents[1]
load_dotenv(APP.parent / '.env'); sys.path.insert(0, str(APP))
from service.assistant import full_research as full
from service.assistant.runtime import new_state
from service.deep_research_v2.agents.scout import DeepScout


class FullResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_source_supplement_does_not_erase_previous_chart_evidence(self):
        sources={'test':{'url':'test','content':'2024年营收40亿元'}}
        full.remember_source(sources,{'url':'test','content':'2025年营收50亿元'})
        self.assertIn('2024年营收40亿元',sources['test']['content'])
        self.assertIn('2025年营收50亿元',sources['test']['content'])
        before=sources['test']['content']
        full.remember_source(sources,{'url':'test','content':'2025年营收50亿元'})
        self.assertEqual(sources['test']['content'],before)

    async def test_review_truncation_retries_with_compact_output_once(self):
        call=AsyncMock(side_effect=[full.ModelResponseTruncated('截断'),'result'])
        self.assertEqual(await full.bounded_review(call,'system','report',max_tokens=16000),'result')
        self.assertEqual(call.await_count,2)
        self.assertIn('最多6项',call.call_args_list[1].args[1])
        self.assertIn('不得通过',call.call_args_list[1].args[1])
        call=AsyncMock(side_effect=full.ModelResponseTruncated('截断'))
        with self.assertRaises(full.ModelResponseTruncated):await full.bounded_review(call,'system','report')
        self.assertEqual(call.await_count,2)

    async def test_review_does_not_swallow_nontruncation_or_incomplete_result(self):
        call=AsyncMock(side_effect=RuntimeError('transport'))
        with self.assertRaises(RuntimeError):await full.bounded_review(call,'system','report')
        self.assertEqual(call.await_count,1)
        self.assertFalse(full.review_passed({'last_review':{'overall_assessment':{'verdict':'pass','quality_score':9,'review_incomplete':True}}}))

    async def test_reuse_only_charts_still_grounded_in_current_sources(self):
        chart={'verified_data':True,'data_contract':{'title':'测试','type':'line','points':[
            dict(label=str(y),entity='测试企业',period=str(y),metric='销量',unit='万辆',scope='测试',period_basis='全年',
                value_kind='actual',value=n,source_url='https://test/data',quote=f'{y}年全年测试企业销量{n}万辆')
            for y,n in [(2023,40),(2024,50)]]}}
        sources={'https://test/data':{'content':'2023年全年测试企业销量40万辆；2024年全年测试企业销量50万辆'}}
        self.assertEqual(len(full.reusable_charts([chart],sources)),1)
        self.assertEqual(full.reusable_charts([chart],{}),[])
        self.assertEqual(full.reusable_charts([{'image_base64':'legacy'}],sources),[])

    async def test_scoped_writer_does_not_reuse_untraceable_numeric_candidates(self):
        from service.deep_research_v2.agents.writer import LeadWriter
        writer=object.__new__(LeadWriter)
        writer.name='writer'; writer.logger=Mock(); writer.add_message=Mock()
        writer.call_llm=AsyncMock(return_value='{}')
        writer.parse_json_response=Mock(return_value={})
        state={'_scoped_runtime':True,'query':'测试','facts':[], 'insights':[],
            'data_points':[{'name':'无来源营收','value':282.4,'unit':'推测单位'}],
            'charts':[{'data_contract':{'points':[{'quote':'2024年销量40万辆','source_url':'https://test/source'}]}}]}
        await writer._write_section(state, {'id':'s1','title':'测试'})
        prompt=writer.call_llm.call_args.kwargs['user_prompt']
        self.assertNotIn('282.4',prompt)
        self.assertIn('2024年销量40万辆',prompt)
        self.assertIn('https://test/source',prompt)

    async def test_source_context_includes_recent_and_cited_raw_evidence(self):
        deep={'facts':[{'source_url':'https://test/new'}], 'final_report':'正文[引用](https://test/cited)'}
        sources={'https://test/old':{'content':'旧'}, 'https://test/new':{'content':'补查原句'}, 'https://test/cited':{'content':'正文原句'}}
        context=full.source_context(deep, sources)
        self.assertIn('补查原句',context)
        self.assertIn('正文原句',context)
        self.assertNotIn('https://test/old',context)
        sources['local://document/1'] = {'content': '本地早期章节原句'}
        context = full.source_context(deep, sources, text='本章资料：local://document/1')
        self.assertIn('本地早期章节原句', context)

    async def test_chart_reference_is_not_a_source_link(self):
        from service.deep_research_v2.agents.writer import LeadWriter
        writer=object.__new__(LeadWriter)
        value=writer._ground_report({'facts':[], 'charts':[{'id':'c1'}]}, '![市场趋势](c1)')
        self.assertIn('见研究图表',value)
        self.assertNotIn('来源未核验',value)

    async def test_scout_covers_all_sections(self):
        scout = object.__new__(DeepScout); scout.name = 'DeepScout'
        scout.logger = Mock(); scout.add_message = Mock(); scout._emit_search_results_event = Mock()
        scout._fetch_stock_data_if_relevant = AsyncMock()
        scout._research_section = AsyncMock()
        state = {'phase': 'researching', 'search_web': False, 'search_local': True, 'outline': [{'id': str(i), 'status': 'pending'} for i in range(7)], 'facts': []}
        await scout.process(state)
        self.assertEqual(scout._research_section.await_count, 7)

    async def test_scout_cancel_joins_children(self):
        scout = object.__new__(DeepScout); scout.name = 'DeepScout'
        scout.logger = Mock(); scout.add_message = Mock(); scout._emit_search_results_event = Mock()
        stopped = []
        async def wait(*args):
            try: await asyncio.sleep(60)
            finally: stopped.append(True)
        scout._research_section = wait
        state = {'phase': 'researching', 'search_web': False, 'search_local': True, 'outline': [{'status': 'pending'} for _ in range(6)]}
        task = asyncio.create_task(scout.process(state))
        await asyncio.sleep(.02); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(len(stopped), 3)

    async def test_research_route_budget(self):
        scope = {'sources': ['web'], 'kb_ids': [], 'use_memory': False}
        self.assertEqual(new_state('research', scope, full_research=True)['engine'], 'full_research_v2')
        self.assertIsNone(new_state('research', scope, full_research=True)['budget']['max_seconds'])
        self.assertIsNone(new_state('auto', scope, full_research=True)['budget']['max_seconds'])
        self.assertEqual(new_state('sql', scope, full_research=True)['engine'], 'lightweight')

    async def test_full_stages_review_revision_and_resume(self):
        visited = []
        class Expert:
            def __init__(self, role):
                self.role = role; self.client = Mock()
                self._execute_search = AsyncMock(return_value=[{'url':'https://example.com/report', 'title':'fixture', 'summary':'证据 40、50。'}])
                self._execute_local_search = AsyncMock(return_value=[])
                self._execute_code = AsyncMock(return_value={'success':True,'charts':[]})
            async def process(self, deep):
                visited.append(self.role)
                if self.role == 'architect':
                    deep['outline'] = [{'id': 's1', 'title':'趋势', 'status':'pending'}]
                elif self.role == 'scout':
                    await self._execute_search('数据')
                    deep['facts'] = [{'source_url':'https://example.com/report','content':'40、50','related_sections':['s1']}]
                elif self.role == 'writer':
                    deep['draft_sections'] = {'s1':'正文'}
                    deep['final_report'] = '完整结论[来源](https://example.com/report)'
                elif self.role == 'wizard':
                    await self._generate_charts(deep)
                elif self.role == 'critic':
                    passed = visited.count('critic') > 1
                    deep['last_review'] = {'overall_assessment': {'verdict':'pass' if passed else 'needs_revision', 'quality_score':8 if passed else 5},'issues':[]}
                    deep['phase'] = 'completed' if passed else 'revising'
                return deep
        names = [('ChiefArchitect','architect'), ('DeepScout','scout'), ('DataAnalyst','analyst'), ('CodeWizard','wizard'), ('LeadWriter','writer'), ('CriticMaster','critic')]
        from contextlib import ExitStack
        async def chart_stage(deep, sources, model, execute, *, supplement, progress, fetch_source=None):
            await supplement('补查图表', 's1')
            await supplement('补查图表', 's1')
            await progress('图表检查完成')
            self.assertIn('https://example.com/chart',sources)
            self.assertTrue(any(f.get('related_sections')==['s1'] and f.get('source_url')=='https://example.com/chart' for f in deep['facts']))
        with ExitStack() as stack:
            for cls, role in names:
                stack.enter_context(patch.object(full, cls, side_effect=lambda *a, _role=role, **kw: Expert(_role)))
            stack.enter_context(patch.object(full, 'load_context', return_value={'memories':[]}))
            stack.enter_context(patch.object(full, 'compress_history', AsyncMock(return_value={'status': 'not_due'})))
            tool=stack.enter_context(patch.object(full,'execute_tool',AsyncMock(return_value=[{'url':'https://example.com/chart','title':'图表原文','content':'2025年销量60万辆','source':'web'}])))
            stack.enter_context(patch('service.deep_research_v2.chart_contract.grounded_charts',side_effect=chart_stage))
            state = new_state('research', {'sources':['web'],'kb_ids':[],'use_memory':False}, full_research=True)
            snapshots = []
            def interrupt(kind, message, *args):
                snapshots.append(copy.deepcopy(state))
                if kind == 'checkpoint' and state['next_stage'] == 'search': raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await full.run_full_research(state, '研究', 'u', 's', Mock(), interrupt)
            state = snapshots[-1]
            events = []
            await full.run_full_research(state, '研究', 'u', 's', Mock(), lambda *args: events.append(args))
            self.assertEqual(visited, ['architect','scout','analyst','wizard','writer','critic','writer','critic'])
            self.assertEqual(events[-1][2], 'completed')
            self.assertIn('[E1]', events[-1][3])
            self.assertEqual(len(state['review_history']), 2)
            tool.assert_awaited_once()
            self.assertEqual(tool.call_args.args[0],'web_search')
            self.assertEqual(tool.call_args.args[2]['sources'],['web'])
            self.assertTrue(any(e[0]=='chart_progress' for e in events))

            # 已到 finish 的恢复路径也必须重新核对正文，不能沿用旧的高分通过。
            invalid_state = copy.deepcopy(state)
            invalid_state['deep_state']['final_report'] += '预计用户达到7亿人。'
            invalid_events = []
            await full.run_full_research(invalid_state, '研究', 'u', 's', Mock(), lambda *args: invalid_events.append(args))
            self.assertEqual(invalid_events[-1][2], 'partial')
            self.assertTrue(invalid_state['report_validation']['issues'])
            self.assertLess(invalid_state['quality_score'], 7)
            self.assertEqual(invalid_state['deep_state']['last_review']['overall_assessment']['verdict'], 'needs_revision')

            # 补充追溯失败即使模型审核 pass 也不能呈现为无缺口成功。
            state['deep_state']['research_gaps'] = {'fixture': '补充追溯未完成：官方统计'}
            events.clear()
            await full.run_full_research(state, '研究', 'u', 's', Mock(), lambda *args: events.append(args))
            self.assertEqual(events[-1][2], 'partial')
            self.assertIn('补充追溯未完成', events[-1][3])

            # 异常发生在下一次心跳前，也必须同步刚完成的子步骤。
            from service.assistant.llm import ModelResponseError
            async def fail_after_checkpoint(expert, deep):
                deep['outline'][0]['research_checkpoint'] = {'applied': True}
                raise ModelResponseError('fixture')
            state['next_stage'] = 'search'
            with patch.object(Expert, 'process', fail_after_checkpoint), self.assertRaises(ModelResponseError):
                await full.run_full_research(state, '研究', 'u', 's', Mock(), lambda *args: None)
            self.assertTrue(state['deep_state']['outline'][0]['research_checkpoint']['applied'])

    async def test_score_alone_does_not_pass(self):
        self.assertFalse(full.review_passed({'quality_score':9}))
        self.assertFalse(full.review_passed({'last_review': {'overall_assessment': {'verdict':'pass','quality_score':9},'issues':[{'severity':'critical'}]}}))


if __name__ == '__main__': unittest.main()
