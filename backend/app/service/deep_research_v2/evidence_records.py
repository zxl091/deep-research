"""证据记录的时间语义及显式枚举绑定，不用模型结论替代原文定位。"""
import re
from datetime import date
from decimal import Decimal

YEAR_LABEL = '年份（范围未明确）'
DATE_BASES = {'观察日期': 'observed_date', '生效日期': 'effective_date'}


def special_period(point):
    value, basis = str(point['period']), point['period_basis']
    if basis == YEAR_LABEL and re.fullmatch(r'(?:19|20)\d{2}', value):
        return {'start': value, 'end': value, 'granularity': 'year_label'}
    if basis in DATE_BASES:
        date.fromisoformat(value)  # 必须是真实日历日期，不能用发布日期补出报价日期。
        return {'start': value, 'end': value, 'granularity': DATE_BASES[basis]}
    return None


def bind_special_period(point, evidence, observed):
    """仅显式声明的新时间类型走此路径；旧的“全年”仍须全年证据。"""
    expected = special_period(point)
    if not expected:
        return None
    if expected['granularity'] == 'year_label':
        unique = {(p['start'], p['end'], p['granularity']) for p in observed}
        if len(unique) != 1 or observed[0]['granularity'] != 'unknown' or observed[0]['start'][:4] != point['period']:
            raise ValueError('原文年份与记录不一致，或已明确半年/季度，不能改成模糊年份')
        point['period_note'] = '原文仅标注年份，未明确全年或累计范围；不作为全年统计或增长趋势。'
    else:
        year, month, day = map(int, point['period'].split('-'))
        dates = rf'(?:{year}年\s*0?{month}月\s*0?{day}日|{year}-0?{month}-0?{day})'
        compact = re.sub(r'\s+', '', evidence)
        marker = r'(?:生效|起执行|起实行)' if point['period_basis'] == '生效日期' else r'(?:截至|观察于|采集于)'
        if not (re.search(dates + r'[^。；;\n]{0,20}' + marker, compact) or
                re.search(marker + r'[^。；;\n]{0,8}' + dates, compact)):
            raise ValueError('原文缺少明确的价格生效/观察日期，不能以网页发布日期代替')
        point['period_note'] = point['period_basis'] + '：' + point['period'] + '；不代表全年价格。'
    return expected


ENUM_RE = re.compile(
    r'依次为(?P<names>[^，,。；;\n]+)[，,]\s*(?P<metric>[^，,。；;\n]{1,30}?)分别为'
    r'(?P<values>[^，,。；;\n]+)')


def enumeration_rows(text):
    """仅解析有“依次为…指标分别为…”明确顺序的平行列表。"""
    for match in ENUM_RE.finditer(text):
        names = [n.strip() for n in re.split(r'、|以及|和|及', match['names'])]
        values = [n.strip() for n in re.split(r'、|以及|和|及', match['values'])]
        if len(names) < 2 or len(names) != len(values) or len(set(names)) != len(names):
            continue
        parsed = [re.fullmatch(r'(?P<n>\d+(?:\.\d+)?)(?P<unit>[^\d\s]+)', v) for v in values]
        if any(v is None for v in parsed) or len({v['unit'] for v in parsed}) != 1:
            continue
        yield match, [(n, Decimal(v['n']), v['unit']) for n,v in zip(names,parsed)]


def enumeration_matches(point, quote):
    raw = Decimal(str(point.get('original_value', point['value'])))
    matches = []
    for match, rows in enumeration_rows(quote):
        if match['metric'].strip() != point['metric']:
            continue
        if any(n == point.get('entity', point['label']) and v == raw
               and u == point.get('original_unit', point['unit']) for n,v,u in rows):
            matches.append(match)
    return matches[0] if len(matches) == 1 else None


def normalize_year_claim(point, source):
    """只降级未被原文支持的“全年”标签，不改年份、数值或明确半年/季度。"""
    if point.get('period_basis') not in ('全年', '年度') or not re.fullmatch(r'(?:19|20)\d{2}', str(point.get('period', ''))):
        return
    from .chart_contract import original_quote
    from .chart_observations import periods_in
    quote = original_quote(point.get('quote', ''), source.get('content', ''))
    if not quote or point.get('table_ref'):
        return
    offset = source['content'].find(quote)
    observed = periods_in(quote)
    if not observed:
        before = source['content'][max(0, offset-350):offset].split('\n\n')[-1]
        observed = periods_in(before)[-1:]
    unique = {(p['start'], p['end'], p['granularity']) for p in observed}
    if len(unique) == 1 and observed[0]['granularity'] == 'unknown' and observed[0]['start'][:4] == point['period']:
        point['normalizations'] = [{'field':'period_basis','proposed':point['period_basis'], 'verified':YEAR_LABEL,
            'reason':'原文只标注年份，不宣称完整全年范围'}]
        point['period_basis'] = YEAR_LABEL


def explicit_enumeration_records(sources):
    """补足模型漏抽的显式顺序列表；总体与时间只取紧邻列表的原文，不猜指标。"""
    from .chart_observations import periods_in
    for url, source in sources.items():
        content = source.get('content', '')
        for match, rows in enumeration_rows(content):
            before = content[max(0, match.start()-350):match.start()].split('\n\n')[-1]
            # 限定形如“在2025年…中，…依次为”的共同总体，不能用文章标题代替。
            contexts = list(re.finditer(r'((?:19|20)\d{2}年(?:全年|上半年|下半年|前(?:1[0-2]|[1-9])个?月|1[—–~-](?:1[0-2]|[1-9])月)?)([^，,。；;\n]{2,70}?)中[，,]', before))
            if not contexts:
                continue
            context = contexts[-1]
            observed = periods_in(context[1])
            if len(observed) != 1:
                continue
            granularity = observed[0]['granularity']
            basis = {'unknown':YEAR_LABEL, 'year':'全年', 'year_to_date':'累计', 'half_year':'上半年' if '上半年' in context[1] else '下半年'}.get(granularity)
            if not basis:
                continue
            start = match.start()-len(before)+context.start()
            quote = content[start:match.end()]
            # 前面的其他句子可能谈下一年目标，不能污染这一组历史统计。
            lead = re.split(r'[。；;\n]', before[:context.start()])[-1]
            kind_text = lead + quote
            kind = 'forecast' if re.search(r'预测|预计|有望', kind_text) else 'target' if '目标' in kind_text else 'actual'
            for entity, value, unit in rows:
                yield {'entity':entity,'label':entity,'metric':match['metric'].strip(),'unit':unit,'scope':context[2],
                    'period':observed[0]['end'][:7] if granularity == 'year_to_date' else context[1][:4],
                    'period_basis':basis,'value_kind':kind,'value':float(value),
                    'source_url':url,'quote':quote,'extraction_method':'explicit_enumeration'}
