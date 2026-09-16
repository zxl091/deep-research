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
    async def test_reuse_only_charts_still_grounded_in_current_sources(self):
        chart={'verified_data':True,'data_contract':{'title':'测试','type':'line','points':[
            dict(label=str(y),period=str(y),metric='销量',unit='万辆',scope='测试',period_basis='全年',
                value_kind='actual',value=n,source_url='https://test/data',quote=f'{y}年销量{n}万辆')
            for y,n in [(2023,40),(2024,50)]]}}
        sources={'https://test/data':{'content':'2023年销量40万辆；2024年销量50万辆'}}
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
                elif self.role == 'critic':
                    passed = visited.count('critic') > 1
                    deep['last_review'] = {'overall_assessment': {'verdict':'pass' if passed else 'needs_revision', 'quality_score':8 if passed else 5},'issues':[]}
                    deep['phase'] = 'completed' if passed else 'revising'
                return deep
        names = [('ChiefArchitect','architect'), ('DeepScout','scout'), ('DataAnalyst','analyst'), ('CodeWizard','wizard'), ('LeadWriter','writer'), ('CriticMaster','critic')]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for cls, role in names:
                stack.enter_context(patch.object(full, cls, side_effect=lambda *a, _role=role, **kw: Expert(_role)))
            stack.enter_context(patch.object(full, 'load_context', return_value={'memories':[]}))
            stack.enter_context(patch.object(full, 'compress_history', AsyncMock(return_value={'status': 'not_due'})))
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

    async def test_score_alone_does_not_pass(self):
        self.assertFalse(full.review_passed({'quality_score':9}))
        self.assertFalse(full.review_passed({'last_review': {'overall_assessment': {'verdict':'pass','quality_score':9},'issues':[{'severity':'critical'}]}}))


if __name__ == '__main__': unittest.main()
