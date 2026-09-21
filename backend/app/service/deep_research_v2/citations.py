"""将实际检索过的 URL 统一成引用；不猜测残缺链接对应哪份资料。"""
import re


# 单个正则从左到右处理，避免把新插入的 Markdown 链接再次替换。
TOKEN = re.compile(r'(?P<link>(?<!!)\[(?P<label>[^\]\n]*)\]\((?P<target>[^)\s]+)\))'
                   r'|\[(?P<bracket>(?:https?://|local://|sql://)[^\]\s]+)\]'
                   r'|\[(?P<short>[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[^\]\s]+)\]'
                   r'|(?P<bare>(?:https?://|local://|sql://)[^\s<>\[\]（）。，；！？)]+)')


def normalize_citations(text, sources, numbered=False):
    """sources 为 URL→名称/编号；未知链接显式标注，不删除其后的正文。"""
    aliases = {}
    for url in sources:
        if url.startswith(('http://', 'https://')):
            aliases.setdefault(url.split('://', 1)[1], []).append(url)
    def replace(match):
        url = match['target'] or match['bracket'] or match['bare'] or match['short']
        if match['short'] and len(aliases.get(url, [])) == 1:
            url = aliases[url][0]
        tail = ''
        if match['bare']:
            url = url.rstrip('.,;')
            tail = match['bare'][len(url):]
        label = match['label']
        if url not in sources:
            return ((label if label and not re.match(r'^(?:https?|local|sql)://', label) else '')
                    + '（来源未核验）' + tail)
        if numbered:
            return '[' + sources[url] + ']' + tail
        label = label if label and not re.match(r'^(?:https?|local|sql)://', label) else sources[url]
        label = re.sub(r'[\[\]\n]', '', str(label)) or '原始资料'
        return f'[{label}]({url})' + tail
    result = TOKEN.sub(replace, text or '')
    # 相邻的同一引用只保留一个，保留不同来源的引用。
    if numbered:
        result = re.sub(r'(\[E\d+\])(?:\s*\1)+', r'\1', result)
    return result
