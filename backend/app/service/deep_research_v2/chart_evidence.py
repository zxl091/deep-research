"""图表证据的确定性补全：保留来源边界，不猜测粘连表格中的数值。"""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urljoin

import httpx
from bs4 import BeautifulSoup

PLAN_PERIODS = {'十一五': '2006-2010', '十二五': '2011-2015', '十三五': '2016-2020',
                '十四五': '2021-2025', '十五五': '2026-2030'}


def nearby_context(quote, content, period):
    """补相邻原文中的完整统计期间，半年和季度不能退化为年份。"""
    index = content.find(quote)
    if index < 0:
        return ''
    # 不跨越段落；最多回看两个句子，避免把网页其他年份误当数据期间。
    prefix = content[max(0, index - 350):index].split('\n\n')[-1]
    sentences = re.split(r'(?<=[。！？])', prefix)
    from .chart_observations import periods_in
    expected = periods_in(period)
    recent = sentences[-3:]
    for i in range(len(recent)-1, -1, -1):
        observed = periods_in(recent[i])
        if not observed:
            continue
        # 最近一个含期间的句子决定范围，不能被更远的行业预测污染或覆盖。
        if expected and len({(p['start'], p['end'], p['granularity']) for p in observed}) == 1 and observed[0]['start'][:4] == expected[0]['start'][:4]:
            return ''.join(recent[i:]).strip()
        break
    return ''


def check_period(point, evidence):
    period = point['period']
    years = re.findall(r'(?:19|20)\d{2}', period)
    point.pop('period_note', None)
    if years and all(year in evidence for year in years):
        return
    for name, dates in PLAN_PERIODS.items():
        if period == dates and name in evidence and point['period_basis'] == '五年规划期':
            point['period_note'] = f'原文“{name}”对应 {dates} 年；规划投资不代表实际完成额。'
            return
    # 相对年必须有同一表格的列号和原始单元格佐证，不能单凭模型说“上年”。
    table = point.get('_verified_table')
    if table and re.fullmatch(r'\d{4}', period):
        base = re.findall(r'(?<!\d)((?:19|20)\d{2})\s*年度', table['context'])
        header = table['header']
        if len(set(base)) == 1 and '上年同期' in header and int(period) == int(base[0]) - 1:
            point['period_note'] = f'同表 {base[0]} 年度的“上年同期”列对应 {period} 年。'
            return
    raise ValueError('统计年份未在原句或可核验的期间上下文中出现')


def structured_html(html):
    """保留单元格边界；复杂合并表头保留文本，禁止自行分配列含义。"""
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style', 'nav', 'footer']):
        node.decompose()
    tables = []
    for index, table in enumerate(soup.find_all('table')[:30]):
        rows = []
        for tr in table.find_all('tr')[:150]:
            if tr.find_parent('table') is not table:
                continue
            cells = tr.find_all(['th', 'td'], recursive=False)
            if cells:
                rows.append([c.get_text(' ', strip=True) for c in cells])
        if not rows:
            continue
        heading = table.find_previous(['h1', 'h2', 'h3', 'h4', 'caption'])
        context = heading.get_text(' ', strip=True) if heading else ''
        simple = not table.select('[rowspan], [colspan]')
        header_rows = 0
        for tr in table.find_all('tr'):
            if tr.find_parent('table') is not table:
                continue
            cells = tr.find_all(['th', 'td'], recursive=False)
            if cells and (tr.find_parent('thead') or all(c.name == 'th' and c.get('scope') != 'row' for c in cells)):
                header_rows += 1
            else:
                break
        tables.append({'id': f'table_{index}', 'context': context, 'rows': rows, 'simple': simple, 'header_rows': header_rows})
        rendered = context + '\n' + '\n'.join(' | '.join(row) for row in rows)
        table.replace_with(soup.new_string('\n' + rendered + '\n'))
    return soup.get_text('\n', strip=True)[:100000], tables



async def fetch_table_source(url):
    """仅获取公开 HTTP(S) 原文，限制大小/超时并逐跳检查重定向目的地。"""
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        target = url
        for _ in range(4):
            parsed = urlsplit(target)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
                raise ValueError('只读取公开网页来源')
            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), 0, socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('不读取本地或内网来源')
            async with client.stream('GET', target) as response:
                if response.is_redirect:
                    target = urljoin(target, response.headers['location']); continue
                response.raise_for_status()
                if 'html' not in response.headers.get('content-type', '').lower():
                    raise ValueError('来源不是 HTML 表格页面')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise ValueError('网页超过 2 MB')
                # BeautifulSoup 根据网页声明识别中文编码。
                content, tables = structured_html(bytes(body))
                return {'url': url, 'content': content, 'tables': tables, 'fetch_url': target, 'source': 'web'}
        raise ValueError('网页重定向次数过多')
