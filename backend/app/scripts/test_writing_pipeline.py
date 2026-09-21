"""有界写作与恢复回归，无付费模型或业务库写入。"""
import asyncio
import copy
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.deep_research_v2.agents.writer import LeadWriter
from service.deep_research_v2.writing_pipeline import synthesize, revise, split_text
from service.assistant.llm import ModelResponseTruncated, ModelQuotaExhausted, MODEL_CONTEXT


def writer():
    value = object.__new__(LeadWriter)
    value.name = 'writer'; value.logger = Mock(); value.add_message = Mock()
    value.call_llm = AsyncMock(return_value=json.dumps({'executive_summary': '摘要', 'conclusions': ['结论'], 'outlook': '展望'}))
    return value


def state():
    return {'query': '测试长报告', 'outline': [{'id': str(i), 'title': '章节'+str(i), 'status': 'drafted'} for i in range(6)],
            'draft_sections': {str(i): ('完整正文。' * 600) + f'尾部标记{i} [资料](https://test/source)' for i in range(6)},
            'facts': [{'content': '事实', 'source_url': 'https://test/source', 'source_name': '原始资料'}],
            'charts': [], 'critic_feedback': [], 'references': [], 'phase': 'writing'}


class WritingTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_report_keeps_all_chapters_and_only_requests_summary(self):
        value, data = writer(), state()
        await synthesize(value, data)
        self.assertGreater(len(data['final_report']), 16000)
        for content in data['draft_sections'].values():
            self.assertIn(content, data['final_report'])
        self.assertEqual(data['writing_manifest']['chapter_ids'], ['0','1','2','3','4','5'])
        self.assertEqual(value.call_llm.call_args.kwargs['max_tokens'], 4000)
        self.assertIn('不要返回 full_report', value.call_llm.call_args.kwargs['user_prompt'])

    async def test_summary_truncation_preserves_full_body_and_discloses_gap(self):
        value, data = writer(), state()
        value.call_llm.side_effect = ModelResponseTruncated('fixture')
        await synthesize(value, data)
        self.assertEqual(value.call_llm.await_count, 2)
        self.assertTrue(data['writing_gaps']['summary'])
        self.assertFalse(data['writing_manifest']['summary_complete'])
        for content in data['draft_sections'].values(): self.assertIn(content, data['final_report'])

    async def test_incomplete_json_is_never_repaired_into_success(self):
        value, data = writer(), state()
        value.call_llm.return_value = '{"executive_summary": "被截断'
        await synthesize(value, data)
        self.assertTrue(data['writing_gaps'])
        self.assertNotIn('被截断', data['final_report'])

    async def test_summary_checkpoint_reused_only_for_same_chapters(self):
        value, data = writer(), state()
        await synthesize(value, data)
        restored = json.loads(json.dumps(data))
        await synthesize(value, restored)
        self.assertEqual(value.call_llm.await_count, 1)
        restored['draft_sections']['0'] += '新增内容'
        await synthesize(value, restored)
        self.assertEqual(value.call_llm.await_count, 2)

    async def test_missing_chapter_does_not_masquerade_as_full_report(self):
        value, data = writer(), state()
        del data['draft_sections']['2']
        with self.assertRaisesRegex(ValueError, '章节正文尚未齐全'):
            await synthesize(value, data)
        value.call_llm.assert_not_awaited()

    async def test_quota_is_not_swallowed_by_summary_fallback(self):
        value, data = writer(), state()
        value.call_llm.side_effect = ModelQuotaExhausted('fixture')
        with self.assertRaises(ModelQuotaExhausted): await synthesize(value, data)
        self.assertNotIn('final_report', data)
        self.assertEqual(value.call_llm.await_count, 1)

    async def test_split_preserves_every_character_including_long_lines(self):
        for text in ['第一段\n\n' * 2000, 'a' * 18000, '表格\n|甲|乙|\n' * 3000]:
            chunks = split_text(text)
            self.assertEqual(''.join(chunks), text)
            self.assertLessEqual(max(map(len,chunks)), 6000)

    async def test_revision_preserves_tail_of_report_over_old_60000_limit(self):
        value, data = writer(), state()
        data['final_report'] = '完整段落\n' * 14000 + '报告最后一句END'
        async def echo(**kwargs):
            body = kwargs['user_prompt'].split('：\n', 1)[1]
            return json.dumps({'revised_content': body})
        value.call_llm.side_effect = echo
        await revise(value, data)
        self.assertTrue(data['final_report'].endswith('报告最后一句END'))
        self.assertGreater(len(data['final_report']), 60000)
        self.assertTrue(all(c.kwargs['max_tokens'] == 6000 for c in value.call_llm.call_args_list))

    async def test_cancel_and_resume_keeps_completed_revision_parts(self):
        value, data = writer(), state()
        data['final_report'] = '第一段\n' * 1300 + '第二段\n' * 1300
        calls = []
        async def interrupted(**kwargs):
            calls.append(MODEL_CONTEXT.get()['step'])
            if len(calls) == 2: raise asyncio.CancelledError()
            return json.dumps({'revised_content': kwargs['user_prompt'].split('：\n', 1)[1]})
        value.call_llm.side_effect = interrupted
        with self.assertRaises(asyncio.CancelledError): await revise(value, data)
        self.assertEqual(list(data['writing_revision']['completed']), ['1'])
        restored = json.loads(json.dumps(data))
        async def resumed(**kwargs):
            calls.append(MODEL_CONTEXT.get()['step'])
            return json.dumps({'revised_content': kwargs['user_prompt'].split('：\n', 1)[1]})
        value.call_llm.side_effect = resumed
        await revise(value, restored)
        self.assertEqual(calls.count('report_revision_1'), 1)
        self.assertEqual(calls.count('report_revision_2'), 2)

    async def test_repeated_truncation_splits_only_failed_part(self):
        value, data = writer(), state()
        data['final_report'] = '可保留内容\n' * 500
        calls = []
        async def response(**kwargs):
            part = MODEL_CONTEXT.get()['step']
            calls.append(part)
            if part == 'report_revision_1': raise ModelResponseTruncated('fixture')
            return json.dumps({'revised_content': kwargs['user_prompt'].split('：\n', 1)[1]})
        value.call_llm.side_effect = response
        await revise(value, data)
        self.assertEqual(calls.count('report_revision_1'), 2)
        self.assertIn('report_revision_1.0', calls)
        self.assertIn('report_revision_1.1', calls)
        self.assertEqual(data['final_report'].count('可保留内容'), 500)

    async def test_reference_only_tail_never_calls_model(self):
        value, data = writer(), state()
        data['final_report'] = '58. [来源甲](https://test/source)\n59. [来源乙](https://test/other)'
        original = data['final_report']
        await revise(value, data)
        value.call_llm.assert_not_awaited()
        self.assertEqual(data['final_report'], original)
        self.assertEqual(data['writing_revision']['status'], 'assembled')

    async def test_invalid_revision_preserves_original_with_gap(self):
        value, data = writer(), state()
        data['final_report'] = '原有正文与结论，仍待审核。'
        value.call_llm.return_value = json.dumps({'wrong_field': '不能替代正文'})
        await revise(value, data)
        self.assertEqual(value.call_llm.await_count, 2)
        self.assertEqual(data['final_report'], '原有正文与结论，仍待审核。')
        self.assertIn('revision_1', data['writing_gaps'])

    async def test_invalid_large_revision_splits_and_recovers(self):
        value, data = writer(), state()
        data['final_report'] = '可保留正文\n' * 200
        async def response(**kwargs):
            if MODEL_CONTEXT.get()['step'] == 'report_revision_1':
                return json.dumps({'wrong_field': 'invalid'})
            return json.dumps({'revised_content': kwargs['user_prompt'].split('：\n', 1)[1]})
        value.call_llm.side_effect = response
        await revise(value, data)
        self.assertEqual(data['final_report'].count('可保留正文'), 200)
        self.assertFalse(data.get('writing_gaps'))

    async def test_revision_quota_error_is_not_content_fallback(self):
        value, data = writer(), state()
        data['final_report'] = '原始正文'
        value.call_llm.side_effect = ModelQuotaExhausted('fixture')
        with self.assertRaises(ModelQuotaExhausted): await revise(value, data)
        self.assertEqual(value.call_llm.await_count, 1)
        self.assertFalse(data.get('writing_gaps'))


if __name__ == '__main__': unittest.main(verbosity=2)
