"""不调用外部服务的故障回归：向量失败、附件解析、检索范围和错误传播。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.docmind_service import extract_document_text, prepare_document_chunks
from service.deep_research_v2.agents.scout import DeepScout
from service.embedding_service import generate_embedding
from service.chat_service import ChatService
from service.checkpoint_service import CheckpointService
import json
from uuid import uuid4
from datetime import datetime
from types import SimpleNamespace
from service.deep_research_v2.state import chart_generation_enabled, create_initial_state
from service.deep_research_v2.agents.wizard import CodeWizard
from service.deep_research_v2.agents.writer import LeadWriter


class CheckpointFailures(unittest.TestCase):
    def test_runtime_queue_excluded_and_values_converted(self):
        uid = uuid4()
        original = {'_message_queue': asyncio.Queue(), '_user_id': str(uid), 'kb_ids':[str(uid)],
                    'facts':[{'id':uid, 'date':datetime(2026, 9, 10)}]}
        clean = CheckpointService()._clean_state_for_storage(original)
        json.dumps(clean)
        self.assertNotIn('_message_queue', clean)
        self.assertEqual(clean['_user_id'], str(uid))
        self.assertEqual(clean['kb_ids'], [str(uid)])
        self.assertEqual(clean['facts'][0]['id'], str(uid))
        self.assertIsInstance(original['_message_queue'], asyncio.Queue)


class RerankFailures(unittest.TestCase):
    def test_scores_follow_original_document_identity(self):
        chat = object.__new__(ChatService)
        chat.openai_api_key = 'synthetic'

        def reversed_results(nodes, query_str):
            nodes[0].score = 0.1
            nodes[1].score = 0.9
            return [nodes[1], nodes[0]]

        with patch('service.chat_service.DashScopeRerank') as reranker:
            reranker.return_value.postprocess_nodes.side_effect = reversed_results
            scores = chat.rerank_similarity('query', [{'content':'irrelevant'}, {'content':'relevant'}])
            self.assertEqual(scores, [0.1, 0.9])


class DocumentFailures(unittest.TestCase):
    def test_missing_vector_rejected(self):
        for invalid in [None, [None], [[1.0]], [[float('nan')]*1024]]:
            with self.subTest(vector_type=type(invalid).__name__), \
                 patch('service.docmind_service.extract_document_text', return_value='test body'), \
                 patch('service.docmind_service.generate_embedding', return_value=invalid):
                with self.assertRaisesRegex(RuntimeError, '向量生成失败'):
                    prepare_document_chunks('unused', 'report.pdf', 'doc', 'kb')

    def test_pdf_parser_failure_is_not_placeholder_success(self):
        with patch('service.docmind_service.DocMindService') as service:
            service.return_value.submit_job.return_value = None
            with self.assertRaisesRegex(RuntimeError, '提交失败'):
                extract_document_text('unused', 'report.pdf')

    def test_empty_parsed_document_fails(self):
        with patch('service.docmind_service.DocMindService') as service:
            service.return_value.submit_job.return_value = 'task'
            service.return_value.wait_for_completion.return_value = True
            service.return_value.collect_all_results.return_value = '  '
            with self.assertRaisesRegex(ValueError, '正文'):
                extract_document_text('unused', 'report.docx')

    def test_text_does_not_need_cloud_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'a.txt'
            file.write_text('合成测试文本', encoding='utf-8-sig')
            with patch('service.docmind_service.DocMindService') as service:
                self.assertEqual(extract_document_text(str(file), file.name), '合成测试文本')
                service.assert_not_called()

    def test_partial_embedding_response_fails(self):
        with patch('service.embedding_service.OpenAI') as factory:
            client = factory.return_value.__enter__.return_value
            client.embeddings.create.return_value.data = []
            self.assertIsNone(generate_embedding(['first', 'second'], api_key='synthetic'))
            factory.return_value.__exit__.assert_called_once()


class SearchScopeFailures(unittest.IsolatedAsyncioTestCase):
    def scout(self):
        scout = object.__new__(DeepScout)
        scout.logger = MagicMock()
        return scout

    async def test_local_followup_never_calls_web(self):
        scout = self.scout()
        scout._execute_search = AsyncMock(side_effect=AssertionError('unexpected web call'))
        scout._execute_local_search = AsyncMock(return_value=[{'title':'local'}])
        result = await scout._search_for_state('query', {'search_web':False,'search_local':True})
        self.assertEqual(result, [{'title':'local'}])
        scout._execute_search.assert_not_called()

    async def test_no_user_cannot_search_global_collection(self):
        with patch('service.knowledge_index.search_user_knowledge') as search:
            self.assertEqual(await self.scout()._execute_local_search('query', state={}), [])
            search.assert_not_called()

    async def test_retrieval_failure_is_propagated(self):
        with patch('service.knowledge_index.search_user_knowledge', side_effect=RuntimeError('backend unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'backend unavailable'):
                await self.scout()._execute_local_search('query', state={'_user_id':'user','kb_ids':['kb']})

    async def test_empty_model_response_raises(self):
        scout=self.scout()
        scout.model='glm-5.2'
        scout.client=MagicMock()
        scout.client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=''),finish_reason='length')])
        with patch.dict('os.environ', {'RESEARCH_ENABLE_THINKING':'false'}):
            with self.assertRaisesRegex(RuntimeError, '空正文'):
                await scout.call_llm('system', 'query')
        self.assertEqual(scout.client.chat.completions.create.call_args.kwargs['extra_body'], {'enable_thinking':False})

    async def test_explicit_no_charts_skips_code_generation(self):
        self.assertFalse(chart_generation_enabled('核验资料，不需要图表，不使用互联网。'))
        self.assertTrue(chart_generation_enabled('分析趋势并生成图表'))
        wizard=object.__new__(CodeWizard)
        wizard.logger=MagicMock()
        wizard._analyze_data=AsyncMock(side_effect=AssertionError('unexpected code generation'))
        state=create_initial_state('核验资料，不需要图表。', 'synthetic')
        self.assertIs(await wizard.process(state), state)
        wizard._analyze_data.assert_not_called()

    async def test_local_evidence_preserves_source_and_original_text(self):
        scout=self.scout()
        scout.call_llm=AsyncMock(side_effect=AssertionError('Do not rewrite local evidence'))
        sources=[{'url':'local://kb/k/doc', 'title':'same.pdf','summary':'The only fact: pilot 47.'}]
        result=await scout._analyze_search_results('query', {}, sources)
        self.assertEqual(result['extracted_facts'][0]['source_url'], sources[0]['url'])
        self.assertEqual(result['extracted_facts'][0]['content'], sources[0]['summary'])
        scout.call_llm.assert_not_called()

    async def test_writer_rejects_invented_source_links(self):
        writer=object.__new__(LeadWriter)
        state={'facts':[{'source_url':'local://kb/k/doc','source_name':'same.pdf'}]}
        result=writer._ground_report(state, '[真实](local://kb/k/doc) [虚构](https://fake.example/report)')
        self.assertIn('[真实](local://kb/k/doc)', result)
        self.assertNotIn('https://fake.example', result)
        self.assertIn('来源未核验', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
