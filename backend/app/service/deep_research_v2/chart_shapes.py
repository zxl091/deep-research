"""图表的数据维度约束，不涉及检索、模型或执行代码。"""
import re

CHART_TYPES = {'line', 'bar', 'horizontal_bar', 'pie', 'donut', 'grouped_bar',
               'stacked_bar', 'radar', 'scatter', 'heatmap'}


def period_position(period):
    if '-H' in period:
        y, h = period.split('-H'); return int(y) + (int(h) - 1) / 2
    if '-Q' in period:
        y, q = period.split('-Q'); return int(y) + (int(q) - 1) / 4
    if '-' in period:
        y, m = period.split('-'); return int(y) + (int(m) - 1) / 12
    return int(period)


def validate_shape(spec, dimensions):
    rows, kind = spec['points'], spec.get('type')
    if kind not in CHART_TYPES:
        raise ValueError('不支持的图表类型')
    if any(not isinstance(p.get('label'), str) or not p['label'].strip() for p in rows):
        raise ValueError('每个数据点需要明确的对象标签')
    if any(not isinstance(p.get('series', ''), str) for p in rows):
        raise ValueError('系列名称必须是字符串')
    if kind == 'grouped_bar' and len({p.get('series', '') for p in rows}) == 1:
        spec['type'] = kind = 'bar'
        spec['type_adjustment'] = '仅有一个数据系列，使用普通柱状图，不构造额外分组。'
    if kind == 'line':
        if any(p['observation']['period']['granularity'] in ('year_label', 'observed_date', 'effective_date') for p in rows):
            raise ValueError('模糊年份或不同日期的报价不自动构造统计趋势；保留数据表')
        if any(not re.fullmatch(r'\d{4}(?:-(?:0[1-9]|1[0-2])|-Q[1-4]|-H[12])?', p['period']) for p in rows):
            raise ValueError('趋势期间请使用 YYYY、YYYY-MM、YYYY-Q1 或 YYYY-H1/H2 格式')
        keys = [(p.get('series', ''), p['period']) for p in rows]
        if len(keys) != len(set(keys)):
            raise ValueError('趋势图同一序列不能重复统计期间')
        sizes = [sum(p.get('series', '') == s for p in rows) for s in {p.get('series', '') for p in rows}]
        if min(sizes) < 2:
            raise ValueError('每条时间序列至少需要两个时点')
        rows.sort(key=lambda p: (p.get('series', ''), period_position(p['period'])))
        if 2 in sizes:
            spec['display_note'] = '仅两个时点的序列使用离散标记，不连线、不补齐缺失年份。'
        return
    from .chart_evidence import PLAN_PERIODS
    planning_comparison = (kind in ('bar', 'horizontal_bar')
        and all(p['period_basis'] == '五年规划期' and PLAN_PERIODS.get(p['label']) == p['period'] for p in rows)
        and len({p['period'] for p in rows}) == len(rows))
    if planning_comparison:
        spec['display_note'] = '按五年规划期比较同口径金额；规划目标与实际完成额不能混用，约数保留约号。'
    if len({p['period'] for p in rows}) != 1 and not planning_comparison:
        raise ValueError('横向对比必须使用相同统计期间')
    if kind == 'scatter':
        if {p.get('series') for p in rows} != {'x', 'y'}:
            raise ValueError('散点图需要成对的 x/y 原文数值')
        if len({d[2:] for d in dimensions}) != 1:
            raise ValueError('散点图对象范围、统计口径与实际/预测属性必须一致')
        for axis in ('x', 'y'):
            if len({(p['metric'], p['unit']) for p in rows if p['series'] == axis}) != 1:
                raise ValueError('同一坐标轴必须保持指标和单位一致')
    keys = [(p.get('series', ''), p['label']) for p in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('同一系列与分类坐标不能重复')
    labels = list(dict.fromkeys(p['label'] for p in rows))
    series = list(dict.fromkeys(p.get('series', '') for p in rows))
    if kind in ('grouped_bar', 'stacked_bar', 'heatmap', 'radar', 'scatter'):
        if kind != 'radar' and (len(series) < 2 or '' in series):
            raise ValueError('矩阵图需要至少两个明确命名的系列')
        if len(labels) < (3 if kind == 'radar' else 2) or len(rows) != len(labels) * len(series):
            raise ValueError('图表矩阵或配对数据不完整，不得用零补齐')
    elif len(series) > 1:
        raise ValueError('多系列分类数据应使用分组或堆叠图')
    if kind in ('pie', 'donut', 'stacked_bar', 'radar') and any(p['value'] < 0 for p in rows):
        raise ValueError('构成或雷达图不支持负数')
    if kind in ('pie', 'donut', 'stacked_bar', 'radar', 'scatter', 'grouped_bar') and any(p.get('qualifier') for p in rows):
        raise ValueError('该图型要求精确数值，阈值或约数请使用带限定词的柱状、条形或时间序列图')
    if kind in ('pie', 'donut'):
        if rows[0]['unit'] != '%' or abs(sum(p['value'] for p in rows) - 100) > .2:
            raise ValueError('饼图仅接受原文占比且合计为 100%，不得从不完整分类推算份额')
    if kind == 'stacked_bar':
        if rows[0]['unit'] != '%' or any(abs(sum(p['value'] for p in rows if p['label'] == label) - 100) > .2 for label in labels):
            raise ValueError('堆叠图需为每组完整的 100% 占比构成')
