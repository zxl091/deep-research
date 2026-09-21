"""图表来源选择、证据格式及兼容入口；编排见 chart_flow。"""
import copy
import json
import re
from .chart_contract import validate_charts

POINT_SCHEMA = '''每点使用 {"entity":"原文中数值所属对象的准确名称", "label":"对象/分类", "series":"系列名（单序列可省略）", "metric":"原文指标名称", "unit":"单位", "scope":"共同统计总体", "period":"YYYY / YYYY-MM / YYYY-Q1 / YYYY-H1 / YYYY-H2", "period_basis":"全年/上半年/下半年/季度/月度/累计", "value_kind":"actual/forecast/target", "value":数值, "source_url":"给定URL", "quote":"逐字原句"}。
对象、指标、期间、数值必须存在明确的对应关系；不能只因数字和年份出现在同一段就宣称匹配。
表格优先给table_ref，引用完整数据行；entity必须等于对象列。普通文本须引用包含“对象+指标+数值”的明确短句，并保留该数值的期间前文。代词、跨段推断或歧义保留缺口。
分类标签label须等于entity；多系列可使用series+label组成原文对象名；时间序列可用period作label。不得让图上标签与实际对象不同。
entity只写原文对象，metric只写原文指标，不重复拼接，也不擅自改成同义词。单对象趋势省略series，多对象趋势series必须等于对应entity；不要用指标名称代表不同对象。
只有年份但未说明完整统计期间时，不自动补成全年。统计口径statistical_scope与统计方式aggregation可填原文明确字段，未说明则省略。不要生成observation，系统会从证据位置生成。
quote 必须在所给原文中包含数值。年份/单位若在同一来源表头，额外给 context_quote，逐字摘录表头；不能编写上下文。
仅当提供的原文已有 | 时才逐字保留该单元格分隔符；不要自行将原文换行改写为 |。严禁从 75514.0148.48 一类粘连数字猜测占比。优先引用重新抓取的完整行。
若来源提供 simple=true 的 tables，可给 table_ref={"id":"table_0","row":1,"column":2}，均从0计数，首行为列头；用于核验上年同期列。无法确定所属期间则保留缺口。
五年规划期可用 period=2011-2015、label=十二五、period_basis=五年规划期；柱状图允许比较同口径规划期，value_kind=target，不能冒充实绩。
若换算单位，必须额外给 original_value、original_unit，且只允许元/万元/亿元、千瓦/万千瓦/亿千瓦/MW/GW、吨/万吨、辆/万辆内部的精确换算。不计算原文没有的占比、增长率或评分。
相同图的 metric、unit、scope、period_basis、value_kind 必须一致，实际/预测/目标不能混画。scope 指共同统计总体；分类对象放 label，多系列放 series。
line 可为多条序列，各序列至少2点，不补齐缺失年份；仅2点将用离散标记。分类图使用同一 period；按明确五年规划期比较的柱状图除外。
pie/donut 仅使用原文明确的占比，unit=% 且合计100%，不能从部分数据归一化。stacked_bar 每个 label 下的所有 series 合计100%。
只有部分企业份额时直接选择bar，保留原始百分比；不能补“其他”，不能因为无法做饼图就放弃已有可比较数据。
grouped_bar/heatmap/radar 必须是完整的 label×series 矩阵，不填零。雷达图只使用同一指标单位的3个以上分类轴，不能虚构主观评分。
scatter 使用同一 label 对象的两条记录，series 分别为 x/y，各轴自己的指标、单位一致，范围、期间和实际/预测属性一致。
缺少证据返回 chart:null 和 missing_data、search_queries，不强行凑图。图表标题必须描述实际数据范围。'''


def select_sources(deep, snapshots, plan=None, max_chars=42000):
    """保留原文，用主题相关片段而非固定截取文章开头，优先图表补查来源。"""
    plan = plan or {}
    section = plan.get('section_id')
    related = {f.get('source_url') for f in deep.get('facts', []) if section and section in f.get('related_sections', [])}
    preferred = set(plan.get('source_urls', []))
    keywords = re.findall(r'[A-Za-z][A-Za-z0-9_-]+|[\u4e00-\u9fff]{2,8}', plan.get('data_question', '')+' '+plan.get('title', ''))
    def score(item):
        url, value = item
        body = value.get('content', '')
        return (100 if url in preferred else 0) + (30 if url in related else 0) + sum(3 for w in keywords if w in body) + min(12,len(re.findall(r'\d+(?:\.\d+)?\s*(?:%|亿元|万元|万千瓦|GW)', body)))
    selected, used = {}, 0
    for url, source in sorted(snapshots.items(), key=score, reverse=True)[:80]:
        body = source.get('content', '')
        if not body: continue
        if len(body)>5000:
            spans = [(0,800)]
            hits = [m.start() for m in re.finditer(r'(?:19|20)\d{2}|\d+\s*%',body)]
            hits += [body.find(k) for k in keywords if k in body]
            for pos in sorted(set(hits))[:24]: spans.append((max(0,pos-160),min(len(body),pos+450)))
            merged=[]
            for start,end in sorted(spans):
                if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],end))
                else:merged.append((start,end))
            body='\n[…]\n'.join(source['content'][start:end] for start,end in merged)[:5000]
        if used+len(body)>max_chars: continue
        prepared=dict(source,url=url,content=body)
        if source.get('tables'):
            prepared['tables']=[]
            for table in source['tables'][:5]:
                proposed=dict(prepared,tables=prepared['tables']+[table])
                if used+len(json.dumps(proposed,ensure_ascii=False))<=max_chars:prepared=proposed
        size=len(json.dumps(prepared,ensure_ascii=False))
        if used+size>max_chars:continue
        selected[url]=prepared;used+=size
    return selected


def reusable(charts, snapshots):
    result=[]
    for chart in charts:
        if chart.get('verified_data') and chart.get('data_contract'):
            specs, errors=validate_charts([chart['data_contract']],snapshots)
            if specs and not errors:
                from .chart_inventory import verify_records
                records, issues = verify_records(specs[0]['points'], snapshots)
                if issues or len(records) != len(specs[0]['points']):
                    continue
                item=copy.deepcopy(chart);item['data_contract']=specs[0];result.append(item)
    return result


async def grounded_charts(*args, **kwargs):
    from .chart_flow import grounded_charts as run
    return await run(*args, **kwargs)
