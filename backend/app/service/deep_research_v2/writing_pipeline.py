"""长报告按章节组装、按片段修订，模型单次输出不再承载整篇报告。"""
import hashlib
import json
import re

from service.assistant.llm import MODEL_CONTEXT, ModelResponseError, ModelResponseTruncated


SUMMARY_PROMPT = """根据已完成的章节，仅返回报告的摘要和结尾 JSON：
{"executive_summary":"300—500字","conclusions":["最多5条，每条不超过100字"],"outlook":"不超过300字"}
总输出不超过1500字。不要返回 full_report、章节正文或参考文献列表，程序会原样组装完整章节及来源。
摘要和结论仅概括正文，不得新增数字、预测、客户案例或来源。保留必要的原有引用链接。
跨章节差异要明确说明，缺少证据时保留局限；不要把相互冲突的口径强行合并。
结论按“差异—证据—业务影响—适用条件”归纳，避免重抄各章开头和泛泛的行业展望。
"""

REVISION_PROMPT = """根据审核意见，只修订下面这一个报告片段，保留本片段完整的正文、标题、表格和有效引用。
只处理与本片段有关的问题；没有相关问题则原样返回。不要输出其他章节或整篇报告，不新增无依据的数字。
返回 JSON：{"revised_content":"完整的修订后片段","changes_made":["简短修改说明，最多5条"]}
仅按原文与证据修正，不把审核建议当成新事实。输出正文长度不得超过原片段长度的1.5倍或2000字（取较大者）。
"""


class WritingOutputError(ModelResponseError):
    """当前写作子步骤的输出字段或长度未通过校验。"""


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def split_text(text, limit=6000):
    """保留所有字符；优先在行边界拆分，无变化时 join 后等于原文。"""
    chunks, current = [], ''
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            chunks.append(current); current = ''
        while len(line) > limit:
            chunks.append(line[:limit]); line = line[limit:]
        current += line
    if current:
        chunks.append(current)
    return chunks


async def bounded_json(writer, system, prompt, validate, step, max_tokens=4000):
    """截断或协议不符只紧缩重试一次，不吞额度、网络或取消错误。"""
    for attempt in range(2):
        token = MODEL_CONTEXT.set({**MODEL_CONTEXT.get(), 'step': step, 'output_recovery_attempt': attempt + 1})
        try:
            response = await writer.call_llm(system_prompt=system, user_prompt=prompt,
                                             json_mode=True, temperature=.3, max_tokens=max_tokens)
            # 流式网关已验证完整 JSON；这里不补括号或修复截断响应。
            try:
                result = json.loads(response)
            except (json.JSONDecodeError, TypeError) as exc:
                raise WritingOutputError('写作步骤未返回完整 JSON 对象') from exc
            if not isinstance(result, dict) or not validate(result):
                raise WritingOutputError('写作输出不符合当前片段的字段或长度约束')
            return result
        except (ModelResponseTruncated, WritingOutputError):
            if attempt:
                raise
            prompt += '\n上次输出超长或不符合字段约束：严格只输出当前要求的字段，删去重复解释，不输出整篇报告或额外字段。'
        finally:
            MODEL_CONTEXT.reset(token)


def summary_valid(value):
    return (set(value) <= {'executive_summary', 'conclusions', 'outlook', 'comparison_table'}
            and isinstance(value.get('executive_summary'), str) and bool(value['executive_summary'].strip())
            and isinstance(value.get('outlook'), str)
            and isinstance(value.get('conclusions'), list) and len(value['conclusions']) <= 5
            and all(isinstance(x, str) and len(x) <= 600 for x in value['conclusions'])
            and isinstance(value.get('comparison_table', ''), str) and len(value.get('comparison_table', '')) <= 6000
            and sum(len(x) for x in [value['executive_summary'], value['outlook'], *value['conclusions']]) <= 4500)


async def synthesize(writer, state):
    chapters = [(s['id'], s.get('title', s['id']), state.get('draft_sections', {}).get(s['id'], ''))
                for s in state['outline']]
    if not chapters or any(not body.strip() for _, _, body in chapters):
        raise ModelResponseError('章节正文尚未齐全，不能组装为完整报告')
    # 章节正文不经过再次生成，避免输出限制截掉报告尾部。
    body = '\n\n'.join(f'## {index} {title}\n\n{content}' for index, (_, title, content) in enumerate(chapters, 1))
    key = fingerprint([state['query'], chapters])
    saved = state.setdefault('writing_summary', {})
    result = saved.get('result') if saved.get('key') == key else None
    if result is None:
        writer.add_message(state, 'research_step', {'title': '章节已齐全，生成摘要与结论后组装报告'})
        try:
            comparison = bool(re.search(r'对比|比较|区别|差异|比较维度', state['query']))
            extra = ('\n本题是对比研究。额外返回 comparison_table 字符串：一张 Markdown 对照表，行列分别为用户要求的维度和对象，'
                     '逐项概括已完成章节的证据，每格含简短结论与已有引用；未知写本次未检索到。不新增对象、数值或链接。'
                     '表格后用摘要解释最影响选择的差异，结论说明适用条件。表格不超过1800字。') if comparison else ''
            result = await bounded_json(writer, '你是研究报告主编，只负责摘要和结论。',
                SUMMARY_PROMPT + extra + '\n研究问题：' + state['query'] + '\n已完成章节：\n' + body,
                lambda value: summary_valid(value) and (not comparison or bool(value.get('comparison_table', '').strip())),
                'report_summary', max_tokens=6000 if comparison else 4000)
            state['writing_summary'] = {'key': key, 'result': result}
            state.setdefault('writing_gaps', {}).pop('summary', None)
        except (ModelResponseTruncated, WritingOutputError):
            result = {}
            state.setdefault('writing_gaps', {})['summary'] = '摘要与结论生成未通过长度或格式校验，已完整保留全部章节正文，未将不完整文本作为结果。'
            writer.add_message(state, 'warning', {'title': state['writing_gaps']['summary']})
    pieces = []
    if result.get('executive_summary'):
        pieces.append('## 执行摘要\n\n' + result['executive_summary'])
    if result.get('comparison_table'):
        pieces.append('## 核心对照\n\n' + result['comparison_table'])
    pieces.append(body)
    if result.get('conclusions') or result.get('outlook'):
        pieces.append('## 结论与展望\n\n' + '\n'.join(f'- {x}' for x in result.get('conclusions', []))
                      + '\n\n' + result.get('outlook', ''))
    report = '\n\n'.join(pieces)
    sources = writer._evidence_sources(state)
    cited = list(dict.fromkeys(re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', report)))
    refs = [url for url in cited if url in sources]
    if refs:
        report += '\n\n## 参考文献\n\n' + '\n'.join(f'{i}. [{sources[url]}]({url})' for i, url in enumerate(refs, 1))
    state['final_report'] = writer._ground_report(state, report)
    state['writing_manifest'] = {'mode': 'assembled_chapters', 'chapter_ids': [s[0] for s in chapters],
                                 'body_chars': len(body), 'report_chars': len(state['final_report']),
                                 'summary_complete': bool(result)}
    writer.add_message(state, 'report_draft', {'agent': writer.name, 'content': state['final_report'],
        'executive_summary': result.get('executive_summary', ''), 'conclusions': result.get('conclusions', []),
        'word_count': len(state['final_report']), 'references_count': len(refs)})


async def revise(writer, state):
    original = state.get('final_report', '')
    if not original.strip():
        raise ModelResponseError('没有可修订的报告正文')
    review = state.get('last_review', {})
    feedback = review.get('issues', []) if review else [x for x in state.get('critic_feedback', []) if not x.get('resolved')]
    key = fingerprint([original, feedback])
    if state.get('writing_revision', {}).get('key') != key:
        state['writing_revision'] = {'key': key, 'completed': {}}
    completed = state['writing_revision']['completed']
    parts = split_text(original)
    async def one_part(content, part_id, depth=0):
        if part_id in completed:
            return completed[part_id]
        # 分片尾部可能只剩程序生成的来源目录。它不包含待改写断言，禁止模型扩写成正文。
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if lines and all(re.fullmatch(r'\d+\.\s+\[[^\]\n]+\]\((?:https?://|local://)[^\s)]+\)', line) for line in lines):
            completed[part_id] = content
            writer.add_message(state, 'research_step', {'title': '来源目录已原样保留：' + part_id})
            return content
        max_chars = max(2000, int(len(content) * 1.5))
        prompt = (REVISION_PROMPT + '\n研究问题：' + state['query']
                  + '\n审核意见：' + json.dumps(feedback, ensure_ascii=False)
                  + f'\n当前片段 {part_id}：\n' + content)
        try:
            result = await bounded_json(writer, '你是研究报告编辑，只修订当前片段。',
                prompt, lambda x: isinstance(x.get('revised_content'), str)
                and bool(x['revised_content'].strip()) and len(x['revised_content']) <= max_chars,
                'report_revision_' + part_id, max_tokens=6000)
            revised = writer._ground_report(state, result['revised_content'])
            state.setdefault('writing_gaps', {}).pop('revision_' + part_id, None)
        except (ModelResponseTruncated, WritingOutputError):
            if depth >= 3 or len(content) < 800:
                revised = content
                message = '修订片段 ' + part_id + ' 未通过输出格式或长度校验，保留修订前内容，相关审核问题仍待处理。'
                state.setdefault('writing_gaps', {})['revision_' + part_id] = message
                writer.add_message(state, 'warning', {'title': message})
            else:
                halves = split_text(content, max(400, len(content) // 2))
                revised = '\n\n'.join([await one_part(part, part_id + '.' + str(i), depth + 1) for i, part in enumerate(halves)])
        completed[part_id] = revised
        writer.add_message(state, 'research_step', {'title': '报告修订片段已保存：' + part_id})
        return revised
    revised_parts = [await one_part(part, str(i + 1)) for i, part in enumerate(parts)]
    state['final_report'] = '\n\n'.join(revised_parts)
    state['writing_revision']['status'] = 'assembled'
    # 不能依据模型自称修复就清除问题，交给下一次完整审核。
    writer.add_message(state, 'revision_complete', {'agent': writer.name, 'chunks': len(parts),
        'addressed_issues': [], 'note': '分段修订已完成，等待重新审核'})
