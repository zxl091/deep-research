"""按证据位置绑定对象、指标和统计期间；不把模型字段当作已验证事实。"""
import calendar
import hashlib
import re
from decimal import Decimal


class BindingIssue(ValueError):
    def __init__(self, field, message, code='binding_mismatch'):
        self.field, self.code = field, code
        super().__init__(f'[{code}:{field}] {message}')


def period_value(year, first=1, last=12, granularity='year'):
    year = int(year)
    return {'start': f'{year:04d}-{first:02d}-01',
            'end': f'{year:04d}-{last:02d}-{calendar.monthrange(year, last)[1]:02d}',
            'granularity': granularity}


PERIOD_RE = re.compile(r'(?<!\d)((?:19|20)\d{2})(?:年)?\s*'
    r'(-H[12]|上半年|下半年|前(?:1[0-2]|[1-9])个?月|前[三二一123]季度|第?[一二三四1234]季度|-Q[1-4]|'
    r'1[—–~-](?:[1-9]|1[0-2])月|(?:1[0-2]|[1-9])月|-(?:0[1-9]|1[0-2])(?![\d-])|全年|度)?')


def periods_in(text):
    result = []
    compact = re.sub(r'\s+', '', text)
    from .chart_evidence import PLAN_PERIODS
    for name, dates in PLAN_PERIODS.items():
        if name in compact:
            first, last = dates.split('-')
            result.append({'start': first+'-01-01', 'end': last+'-12-31', 'granularity': 'multi_year'})
    for match in PERIOD_RE.finditer(compact):
        year, suffix = match[1], match[2] or ''
        if suffix in ('上半年', '-H1'):
            value = period_value(year, 1, 6, 'half_year')
        elif suffix in ('下半年', '-H2'):
            value = period_value(year, 7, 12, 'half_year')
        elif '季度' in suffix or suffix.startswith('-Q'):
            number = next((i for i, ch in enumerate('一二三四', 1) if ch in suffix), None)
            number = number or int(re.search(r'\d', suffix)[0])
            cumulative = suffix.startswith('前')
            value = period_value(year, 1 if cumulative else (number - 1) * 3 + 1, number * 3,
                                 'year_to_date' if cumulative else 'quarter')
        elif suffix.endswith('月') or re.fullmatch(r'-\d{2}', suffix):
            months = re.findall(r'\d+', suffix)
            cumulative = len(months) == 2 or suffix.startswith('前')
            month = int(months[-1])
            value = period_value(year, 1 if cumulative else month, month, 'year_to_date' if cumulative else 'month')
        else:
            value = period_value(year)
            # 裸年份只表明年，不能据此自动认定为全年统计。
            if suffix not in ('全年', '度'):
                value['granularity'] = 'unknown'
        result.append(value)
    return result


def requested_period(point):
    token, basis = str(point['period']), point['period_basis']
    from .evidence_records import special_period
    special = special_period(point)
    if special:
        return special
    if re.fullmatch(r'(?:19|20)\d{2}-(?:19|20)\d{2}', token) and basis == '五年规划期':
        first, last = token.split('-')
        if int(last) - int(first) != 4:
            raise BindingIssue('period', '五年规划期必须为连续五个年份')
        return {'start': first+'-01-01', 'end': last+'-12-31', 'granularity': 'multi_year'}
    if re.fullmatch(r'(?:19|20)\d{2}', token):
        suffix = {'全年': '全年', '年度': '年度', '上半年': '上半年', '下半年': '下半年'}.get(basis)
        if not suffix:
            raise BindingIssue('period', '仅有年份无法确定半年、季度或月份', 'missing_field')
        token += suffix
    values = periods_in(token)
    if len(values) != 1 or values[0]['granularity'] == 'unknown':
        raise BindingIssue('period', '期间需要明确起止范围', 'missing_field')
    result = values[0]
    if basis in ('累计', '年初至今') and result['granularity'] == 'month':
        result = period_value(result['start'][:4], 1, int(result['end'][5:7]), 'year_to_date')
    declared = {'全年': 'year', '年度': 'year', '半年度': 'half_year', '上半年': 'half_year', '下半年': 'half_year',
                '季度': 'quarter', '月度': 'month', '月': 'month', '累计': 'year_to_date', '年初至今': 'year_to_date'}
    if basis in declared and declared[basis] != result['granularity']:
        raise BindingIssue('period_basis', '统计方式与期间范围矛盾')
    if basis == '上半年' and result['start'][5:7] != '01' or basis == '下半年' and result['start'][5:7] != '07':
        raise BindingIssue('period_basis', '上、下半年与期间范围矛盾')
    return result


def same_period(expected, observed):
    return all(expected[k] == observed[k] for k in ('start', 'end', 'granularity'))


def bind_table(point, source):
    ref = point['table_ref']
    table = next((t for t in source.get('tables', []) if t['id'] == ref.get('id')), None)
    if not table or not table.get('simple') or table.get('header_rows') != 1:
        raise BindingIssue('table_ref', '需要有明确单行表头的简单表格；复杂表头待解析', 'unsupported_structure')
    row, col = ref.get('row'), ref.get('column')
    if type(row) is not int or type(col) is not int or row < 1 or col < 1:
        raise BindingIssue('table_ref', '数据行列索引无效')
    try:
        cells, headers = table['rows'][row], table['rows'][0]
        cell, header = cells[col], headers[col]
    except IndexError as exc:
        raise BindingIssue('table_ref', '数据行列越界') from exc
    if len(cells) != len(headers):
        raise BindingIssue('table_ref', '行与表头列数不一致', 'unsupported_structure')
    if len(set(headers)) != len(headers):
        raise BindingIssue('table_ref', '表头列名重复，无法唯一定位字段', 'ambiguous_evidence')
    mapping = {re.sub(r'\s+', '', key): value.strip() for key, value in zip(headers, cells)}
    def field(*names):
        found = [mapping[n] for n in names if n in mapping and mapping[n]]
        if len(set(found)) > 1:
            raise BindingIssue(names[0], '表格字段不唯一', 'ambiguous_evidence')
        return found[0] if found else None
    entity = field('对象', '公司', '企业', '地区', '行业', '产品', '业务', '类别', '主体')
    if not entity:
        raise BindingIssue('entity', '表格未提供明确的对象列', 'missing_field')
    claimed = point.get('entity') or point['label']
    if claimed != entity:
        raise BindingIssue('entity', '所选单元格所属对象与模型标注不一致')
    raw = Decimal(str(point.get('original_value', point['value'])))
    if not re.fullmatch(r'-?\d[\d,]*(?:\.\d+)?%?', cell) or Decimal(cell.rstrip('%').replace(',', '')) != raw:
        raise BindingIssue('value', '数值与指定单元格不一致')
    if cell not in point['quote'] or entity not in point['quote']:
        raise BindingIssue('quote', '引用必须包含实际对象单元格和数值单元格')
    context = table.get('context', '')
    metric = field('指标')
    if metric:
        value_header = re.sub(r'[（(][^）)]*[）)]', '', PERIOD_RE.sub('', header)).strip()
        if value_header not in ('', '数值', '值', '金额', '数量', '本报告期', '上年同期'):
            raise BindingIssue('metric', '所选列不是该指标的数值列，不能把同比等派生列作为原值')
    if not metric:
        metric = re.sub(r'[（(][^）)]*[）)]', '', header)
        metric = PERIOD_RE.sub('', metric).replace('年', '').strip()
    if re.sub(r'\s+', '', metric) != re.sub(r'\s+', '', point['metric']):
        raise BindingIssue('metric', '指标与所选数值列不一致')
    unit = field('单位')
    if not unit:
        units = re.findall(r'[（(]([^）)]+)[）)]', header)
        unit = units[-1] if units else None
    if not unit:
        found = re.search(r'单位[：:]\s*([^，。；;\s）)]+)', context)
        unit = found[1] if found else None
    if not unit:
        raise BindingIssue('unit', '表格未提供数值单位', 'missing_field')
    if point.get('original_unit', point['unit']) != unit:
        raise BindingIssue('unit', '原始单位与数值列单位不一致')
    time_text = field('期间', '月份', '统计期间', '年度') or header
    aggregation = field('统计方式')
    observed = periods_in(time_text)
    if not observed:
        observed = periods_in(context)
        if '上年同期' in header and len(observed) == 1:
            p = observed[0];year = int(p['start'][:4]) - 1
            observed = [period_value(year, int(p['start'][5:7]), int(p['end'][5:7]), p['granularity'])]
    expected = requested_period(point)
    if len(observed) == 1 and aggregation in ('累计', '年初至今', '累计值'):
        p = observed[0]
        observed = [period_value(int(p['start'][:4]), 1, int(p['end'][5:7]), 'year_to_date')]
    from .evidence_records import bind_special_period
    special = bind_special_period(point, context + '\n' + time_text, observed)
    if special:
        observed = [special]
    if len(observed) != 1 or observed[0]['granularity'] == 'unknown':
        raise BindingIssue('period', '表格未明确该单元格的统计期间', 'missing_field')
    if not same_period(expected, observed[0]):
        raise BindingIssue('period', '模型期间与表格实际期间不一致')
    scope = field('统计口径', '口径')
    kind_text = field('数据属性') or context
    source_kind = next((value for word, value in [('预测', 'forecast'), ('目标', 'target'), ('实际', 'actual')] if word in kind_text), None)
    if source_kind and source_kind != point['value_kind']:
        raise BindingIssue('value_kind', '模型的数据属性与来源明确标注不一致')
    for name, value in [('statistical_scope', scope), ('aggregation', aggregation)]:
        if point.get(name) is not None and point[name] != value:
            raise BindingIssue(name, '模型口径与来源字段不一致或来源未说明')
    unknown = [name for name, value in [('statistical_scope', scope), ('aggregation', aggregation)] if value is None]
    point['observation'] = {'schema_version': 2, 'entity': entity, 'metric': metric, 'period': expected,
        'value': point['value'], 'unit': point['unit'], 'value_kind': point['value_kind'], 'source_value_kind': source_kind,
        'statistical_scope': scope, 'aggregation': aggregation, 'binding': 'table_cell',
        'source_url': point['source_url'], 'anchor': dict(table_id=table['id'], row=row, column=col),
        'unknown_fields': unknown}
    point['observation']['source_sha256'] = hashlib.sha256(source['content'].encode()).hexdigest()
    point['_verified_table'] = {'context': context, 'header': header}
    return context + '\n' + header + '\n' + unit


def bind_text(point, source):
    """只接受同一数值短句中可直接定位的对象与指标，复杂语义明确留缺口。"""
    quote = point['quote']
    entity = point.get('entity') or point.get('label')
    if not entity or re.fullmatch(r'\d{4}(?:-.+)?', entity):
        entity = point.get('scope')
    if not entity:
        raise BindingIssue('entity', '缺少数据所属对象', 'missing_field')
    metric = point['metric']
    raw = Decimal(str(point.get('original_value', point['value'])))
    offset = source['content'].find(quote)
    adjacent = source['content'][max(0, offset-350):offset].split('\n\n')[-1]
    # 网页内联标签常被解析为单换行；保留原文位置，但不拆开同一句的对象和数值。
    # 双换行仍是段落边界，不能跨段拼接一个数据关系。
    clauses = list(re.finditer(r'(?:[^，。；;\n,]|\n(?!\n)(?<!\n\n)|(?<=\d),(?=\d))+', quote))
    candidates = []
    from .evidence_records import enumeration_matches
    enumeration = enumeration_matches(point, quote)
    if enumeration:
        candidates.append((enumeration, None))
    for clause in clauses:
        if candidates and candidates[0][1] is None:
            break
        sentence = re.sub(r'\s+', '', clause[0])
        # 自然语言份额常写成“甲以26%的份额...”或“甲(26%)、乙(19%)”。
        # 只扩展这一可逐字绑定的句式，指标仍必须由同段原文给出。
        context = re.sub(r'\s+', '', adjacent + quote[:clause.start()])
        metric_supported = metric in context or metric in sentence
        if metric == '市场份额':
            metric_supported = metric_supported or ('市场' in context and '份额' in context + sentence)
        if metric_supported and point.get('original_unit', point['unit']) == '%' and metric.endswith(('份额', '占比')):
            lead = r'^(?:其中|包括|以及|和|、|\s)*' + re.escape(entity)
            patterns = [lead + r'以(?P<n>\d+(?:\.\d+)?)%的(?:市场)?份额',
                        lead + r'(?:的)?(?:市场份额|份额|占比)(?:为|达|达到|约为)?(?P<n>\d+(?:\.\d+)?)%',
                        r'(?:^|[、和])' + re.escape(entity) + r'[（(](?P<n>\d+(?:\.\d+)?)%[）)]']
            for pattern in patterns:
                found = re.search(pattern, sentence)
                if found and Decimal(found['n']) == raw:
                    candidates.append((clause, found))
                    break
            if candidates and candidates[-1][0] is clause:
                continue
        if entity not in sentence or metric not in sentence:
            continue
        for number in re.finditer(r'(?<![\d.])-?\d[\d,]*(?:\.\d+)?(?![\d.])', sentence):
            if Decimal(number[0].replace(',', '')) != raw:
                continue
            prefix = sentence[:number.start()]
            entity_at, metric_at = prefix.rfind(entity), prefix.rfind(metric)
            if entity_at < 0 or metric_at < entity_at:
                continue
            link = PERIOD_RE.sub('', prefix[entity_at + len(entity):metric_at]).strip('，,：: ')
            from .chart_evidence import PLAN_PERIODS
            for name in PLAN_PERIODS:
                link = link.replace(name + '期间', '').replace('“' + name + '”期间', '')
            if link not in ('', '的'):
                continue
            between = prefix[metric_at + len(metric):]
            if not re.fullmatch(r'(?:为|达|达到|目标|记录|：|:|约|约为|大约|近|不低于|不少于|至少|不高于|不超过|至多|超过|超出|超|高于|大于|低于|小于|不足)*', between):
                continue
            unit = point.get('original_unit', point['unit'])
            tail = sentence[number.end():]
            if not re.match(re.escape(unit) + r'(?=$|以上|以下|左右|上下|以内|及以上|及以下)', tail):
                # 表头单位仅允许从实际相邻原文取回，不能从别处借一个同名单位。
                if tail or not re.search(r'单位[：:]\s*' + re.escape(unit) + r'(?=$|[）)\s])', adjacent):
                    continue
            candidates.append((clause, number))
    if len(candidates) != 1:
        raise BindingIssue('entity/metric/value', '无法唯一确认该对象、指标与数值的对应关系',
                           'missing_field' if not candidates else 'ambiguous_evidence')
    clause, number = candidates[0]
    preceding = (adjacent + quote[:clause.start()])[-350:].split('\n\n')[-1]
    observed = periods_in(clause[0])
    if not observed:
        observed = periods_in(preceding)
        observed = observed[-1:]
    expected = requested_period(point)
    from .evidence_records import bind_special_period
    special = bind_special_period(point, preceding + clause[0], observed)
    if special:
        observed = [special]
    if len(observed) != 1 or observed[0]['granularity'] == 'unknown':
        raise BindingIssue('period', '原文未明确该数值的完整统计期间', 'missing_field')
    if not same_period(expected, observed[0]):
        raise BindingIssue('period', '原文统计期间与图表标注不一致')
    point['observation'] = {'schema_version': 2, 'entity': entity, 'metric': metric,
        'period': expected, 'value': point['value'], 'unit': point['unit'],
        'statistical_scope': None, 'aggregation': None, 'value_kind': point['value_kind'],
        'binding': 'text_clause', 'source_url': point['source_url'],
        'anchor': {'start': offset + clause.start(), 'end': offset + clause.end()},
        'source_sha256': hashlib.sha256(source['content'].encode()).hexdigest(),
        'unknown_fields': ['statistical_scope', 'aggregation']}
    point['observation']['context_anchor'] = {'start': max(0, offset + clause.start() - len(preceding)), 'end': offset + clause.start()}
