"""图表先验证数据与口径，再由固定绘图代码在容器中渲染。"""
import copy
import json
import re
from decimal import Decimal, InvalidOperation
from uuid import uuid4

MAX_POINTS = 120
CONVERSIONS = [dict(元=1, 万元=10000, 亿元=100000000),
               {'千瓦': 1, '万千瓦': 10000, '亿千瓦': 100000000, 'MW': 1000, 'GW': 1000000},
               {'吨': 1, '万吨': 10000}, {'辆': 1, '万辆': 10000}]


def original_quote(quote, content, min_chars=8):
    """容忍排版空白和中英文标点差异，最终返回来源中的真实原句。"""
    translation = str.maketrans({'，': ',', '；': ';', '：': ':', '。': '.', '（': '(', '）': ')'})
    def normalized(value):
        letters, offsets = [], []
        for i, char in enumerate(value):
            if not char.isspace():
                letters.append(char.translate(translation)); offsets.append(i)
        return ''.join(letters), offsets
    needle, _ = normalized(quote)
    haystack, offsets = normalized(content)
    index = haystack.find(needle)
    if len(needle) < min_chars or index < 0:
        return None
    return content[offsets[index]:offsets[index + len(needle) - 1] + 1]


def validate_point(point, sources):
    point.pop('observation', None)
    source = sources.get(point['source_url'])
    quote = point['quote'].strip()
    quote = original_quote(quote, source['content'], 1 if point.get('table_ref') else 8) if source else None
    if not quote:
        raise ValueError('数据原句不在检索结果中')
    point['quote'] = quote
    if re.search(r'\d+\.\d+\.\d+', quote):
        raise ValueError('表格数字粘连，需重新读取独立单元格，不能猜测拆分')
    context = ''
    if point.get('context_quote'):
        context = original_quote(point['context_quote'], source['content'])
        if not context:
            raise ValueError('表头或上下文原句不在同一来源中')
        point['context_quote'] = context
    from service.deep_research_v2.chart_evidence import nearby_context, check_period
    from service.deep_research_v2.chart_observations import bind_table, bind_text
    if not context:
        context = nearby_context(quote, source['content'], str(point.get('period', '')))
        if context:
            point['context_quote'] = context
    evidence = quote + '\n' + context
    if point.get('table_ref'):
        evidence += '\n' + bind_table(point, source)
    value = Decimal(str(point['value']))
    raw = Decimal(str(point.get('original_value', point['value'])))
    raw_unit = point.get('original_unit', point['unit'])
    numbers = re.findall(r'(?<![\d.])-?\d[\d,]*(?:\.\d+)?(?![\d.])', quote)
    if not value.is_finite() or not raw.is_finite() or raw not in [Decimal(n.replace(',', '')) for n in numbers]:
        raise ValueError('数值不在引用原句中，禁止补值或暗中换算')
    if not raw_unit or raw_unit not in evidence:
        raise ValueError('单位缺失或无法从原句确认')
    if raw_unit != point['unit']:
        group = next((g for g in CONVERSIONS if raw_unit in g and point['unit'] in g), None)
        if not group or 'original_value' not in point:
            raise ValueError('单位转换必须保留原始数值及受支持的原始单位')
        expected = raw * Decimal(group[raw_unit]) / Decimal(group[point['unit']])
        if value != expected:
            raise ValueError('单位换算结果不正确')
        point['conversion_note'] = f"{raw} {raw_unit} = {value} {point['unit']}"
    elif raw != value:
        raise ValueError('未转换单位时不能修改数值')
    if not point.get('period') or not point.get('metric') or not point.get('scope') or not point.get('period_basis'):
        raise ValueError('指标、地区、统计期间或口径缺失')
    check_period(point, evidence)
    point.pop('_verified_table', None)
    kind = point.get('value_kind')
    if kind not in ('actual', 'forecast', 'target'):
        raise ValueError('必须区分实际、预测和目标')
    if kind == 'actual' and re.search(r'预计|预测|有望|目标|将达|将超过', evidence):
        raise ValueError('预测或目标不能标作实绩')
    # 限定词由原句推导，不直接信任模型标注；单位换算沿用原数值的限定。
    number_pattern = '(?:' + '|'.join(re.escape(n) for n in numbers if Decimal(n.replace(',', '')) == raw) + ')'
    quantity = number_pattern + r'\s*' + re.escape(raw_unit)
    point.pop('qualifier', None)
    if re.search(r'(?:不低于|不少于|至少)\s*' + quantity + '|' + quantity + r'\s*(?:以上|及以上)', quote):
        point['qualifier'] = 'at_least'
    elif re.search(r'(?:不高于|不超过|至多)\s*' + quantity + '|' + quantity + r'\s*(?:以下|以内)', quote):
        point['qualifier'] = 'at_most'
    elif re.search(r'(?:超过|超出|超|高于|大于)\s*' + quantity, quote):
        point['qualifier'] = 'more_than'
    elif re.search(r'(?:低于|小于|不足)\s*' + quantity, quote):
        point['qualifier'] = 'less_than'
    elif re.search(r'(?:约为|大约|约|近)\s*' + quantity + '|' + quantity + r'\s*(?:左右|上下)', quote):
        point['qualifier'] = 'approximate'
    point['value'] = float(value)
    if not point.get('table_ref'):
        bind_text(point, source)
    observation = point['observation']
    observation.update(value=point['value'], original_value=float(raw), original_unit=raw_unit)
    return (point['metric'], point['unit'], point['scope'], observation['period']['granularity'], kind,
            observation['statistical_scope'], observation['aggregation'])


def validate_charts(specs, sources, diagnostics=None):
    valid, errors = [], []
    if not isinstance(specs, list):
        return [], ['charts 必须为数组']
    from service.deep_research_v2.chart_shapes import validate_shape
    for index, proposed in enumerate(specs[:8]):
        try:
            spec = copy.deepcopy(proposed)
            rows = spec['points']
            if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_POINTS:
                raise ValueError(f'每张图需要 2–{MAX_POINTS} 个数据点')
            dimensions = []
            for point_index, point in enumerate(rows):
                dimensions.append(validate_point(point, sources))
            bound_entities = {point['observation']['entity'] for point in rows}
            for point in rows:
                from service.deep_research_v2.chart_observations import BindingIssue
                entity = point['observation']['entity']
                from service.deep_research_v2.chart_evidence import PLAN_PERIODS
                temporal_label = (spec.get('type') == 'line' and point['label'] == point['period']) or PLAN_PERIODS.get(point['label']) == point['period']
                if not temporal_label and point['label'] != entity and point.get('series', '') + point['label'] != entity:
                    raise BindingIssue('label', '图上显示的对象与已绑定的数据对象不一致')
                series = point.get('series', '')
                if spec.get('type') == 'line' and len(bound_entities) == 1 and series == point['metric']:
                    # 单对象趋势中以指标作图例只是冗余命名；不能据此合并不同对象。
                    point['series'] = series = entity
                if series and not (spec.get('type') == 'scatter' and series in ('x', 'y')) and series != entity and series + point['label'] != entity:
                    raise BindingIssue('series', '图例系列名与已绑定对象不一致')
            if spec.get('type') != 'scatter' and len(set(dimensions)) != 1:
                raise ValueError('一张图只能比较相同指标、单位、地区及统计口径，不得混画预测和实绩')
            validate_shape(spec, dimensions)
            valid.append(spec)
        except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation) as exc:
            errors.append(f'图 {index + 1}：{exc}')
            if diagnostics is not None:
                diagnostics.append({'chart_index': index, 'field': getattr(exc, 'field', None),
                                    'code': getattr(exc, 'code', 'data_validation'), 'message': str(exc)})
    return valid, errors


def recover_partial_series(proposed, sources):
    """保留已有证据充分的趋势点；不修饰数值、单位或来源，也不补齐缺口。"""
    recovered, seen = [], set()
    for spec in proposed if isinstance(proposed, list) else []:
        if not isinstance(spec, dict) or spec.get('type') not in ('line', 'bar', 'horizontal_bar', 'pie', 'donut'):
            continue
        rows = spec.get('points')
        if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_POINTS:
            continue
        groups, omitted = {}, []
        for original in rows:
            point = copy.deepcopy(original)
            try:
                dimensions = validate_point(point, sources)
                # 分类图也按期间隔离；绝不把跨年的地区值当作同年排名。
                key = dimensions + (() if spec['type'] == 'line' else (point['period'],))
                groups.setdefault(key, []).append(point)
            except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation) as exc:
                omitted.append(str(exc))
        for points in groups.values():
            incomplete_share = spec['type'] in ('pie', 'donut') and all(p['unit'] == '%' and 0 <= p['value'] <= 100 for p in points) and 0 < sum(p['value'] for p in points) < 99.8
            if len(points) < 2 or (not omitted and len(groups) == 1 and not incomplete_share):
                continue
            candidate = dict(spec, points=points)
            if incomplete_share:
                candidate['type'] = 'bar'
                candidate['type_adjustment'] = '原文仅支持部分对象的占比，改用柱状比较；不归一化、不补其他份额。'
            accepted, errors = validate_charts([candidate], sources)
            if errors or not accepted:
                continue
            candidate = accepted[0]
            fingerprint = tuple((p['metric'], p['scope'], p['period_basis'], p['value_kind'], p['unit'], p['period'], p.get('label'), p.get('series'), p['value']) for p in points)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            periods = '、'.join(dict.fromkeys(p['period'] if spec['type'] == 'line' else p['label'] for p in points))
            kind = {'actual':'实际值','forecast':'预测值','target':'目标值'}[points[0]['value_kind']]
            candidate['title'] = f"{points[0]['scope']} · {points[0]['metric']}（{kind}；{periods}）"
            candidate['coverage_note'] = f'已剔除 {len(rows)-len(points)} 个缺少合格原文或统计口径不同的数据点；仅展示标题列出的范围，不代表完整序列或全部对象。'
            if incomplete_share:
                candidate['coverage_note'] += ' 原占比保持不变，合计不足100%，不代表完整市场构成。'
            candidate['omitted_reasons'] = omitted + (['其他指标、期间或实际/预测/目标口径的数据单独处理'] if len(groups)>1 else [])
            recovered.append(candidate)
    return recovered[:8]


def render_code(spec):
    from service.deep_research_v2.chart_renderer import render_code as render
    return render(spec)


async def grounded_charts(deep, snapshots, model, execute_code, **kwargs):
    from service.deep_research_v2.chart_pipeline import grounded_charts as generate
    return await generate(deep, snapshots, model, execute_code, **kwargs)
