"""可复现的数字依据检查；语义、统计口径仍由审核专家核对。"""
import ast
import copy
import re
import unicodedata
from decimal import Decimal, InvalidOperation


UNITS = r'万亿元|万亿美元|亿美元|亿元|万元|美元|元|亿人|万人|人|亿家|万家|家|万款|款|个百分点|万个|个|万辆|辆|万吨|吨|EFLOPS|%|倍'
# 中文不属于数字边界，不能用 \w 排除它。
QUANTITY = re.compile(r'(?<![\d.A-Za-z])(-?\d[\d,]*(?:\.\d+)?)\s*(' + UNITS + r')', re.I)
FORECAST = re.compile(r'预计|预测|有望|目标|将达|将超|到\d{4}年')
LINK = re.compile(r'(?<!!)\[[^\]]+\]\(([^)]+)\)')


def quantities(text):
    text = unicodedata.normalize('NFKC', text or '')
    # 只补明确货币语境中的省略单位，不把“5700万用户”等数量改成金额。
    text = re.sub(r'(\d+(?:\.\d+)?)(万|亿)(?=(?:中标项目金额|中标金额|收入|营收|人民币))', r'\1\2元', text)
    text = re.sub(r'((?:金额|营收|收入)(?:为|达|约|达到)?\s*\d+(?:\.\d+)?)(万|亿)(?=$|[，。；、\s])', r'\1\2元', text)
    # 精确二分之一可换写为50%；“超过/不足一半”不等价于精确50%。
    text = re.sub(r'(?<!超过)(?<!不足)(?<!近)(?<!约)(?<!超)(?<!过)(?:一半|二分之一)', '50%', text)
    for match in QUANTITY.finditer(text):
        value = Decimal(match[1].replace(',', ''))
        unit = match[2].upper()
        base = unit
        for prefix, factor in [('万亿', 10**12), ('亿', 10**8), ('万', 10**4)]:
            if unit.startswith(prefix):
                base, value = unit[len(prefix):], value * factor
                break
        yield match, (value, base)


def calculation_supported(clause, key, sources):
    """只接受显式四则算式；操作数须出现在引用原文的带单位数值中。"""
    raw_values = {Decimal(m[1].replace(',', '')) for body in sources for m, _ in quantities(body)}
    formula = re.compile(r'([\d.()+*/×÷−\-\s]{3,100})=\s*(-?\d+(?:\.\d+)?)\s*(' + UNITS + ')', re.I)
    for match in formula.finditer(unicodedata.normalize('NFKC', clause)):
        result = next(quantities(match[2] + match[3]), None)
        if not result or result[1] != key:
            continue
        operands, operators = [], []
        def evaluate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                value = Decimal(str(node.value)); operands.append(value); return value
            if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div):
                operators.append(type(node.op))
                a, b = evaluate(node.left), evaluate(node.right)
                if isinstance(node.op, ast.Add): return a + b
                if isinstance(node.op, ast.Sub): return a - b
                if isinstance(node.op, ast.Mult): return a * b
                return a / b
            raise ValueError('unsupported arithmetic')
        try:
            expression = match[1].strip().translate(str.maketrans({'×': '*', '÷': '/', '−': '-'}))
            computed = evaluate(ast.parse(expression, mode='eval').body)
            allowed = raw_values | ({Decimal(100)} if match[3] == '%' else set())
            # 至少两个有来源的输入，不允许仅把凭空数值写成等式。
            if len(operands) < 2 or not operators or not all(x in allowed for x in operands):
                continue
            if sum(x in raw_values for x in operands) < 2:
                continue
            precision = len(match[2].partition('.')[2])
            if abs(computed - Decimal(match[2])) <= Decimal(5).scaleb(-precision - 1):
                return True
        except (SyntaxError, ValueError, TypeError, ArithmeticError, InvalidOperation):
            continue
    return False


def check_report(report, snapshots):
    """只报告可定位的数字依据缺口，不宣称出现同一数字就证明事实正确。"""
    issues, seen, checked = [], set(), 0
    bodies = {url: value.get('content', '') for url, value in snapshots.items()}
    all_bodies = list(bodies.values())
    # 段落共享引用，避免将句末链接与其前面的句子分离；表格须在本行附来源。
    for paragraph in re.split(r'\n\s*\n', report or ''):
        urls = LINK.findall(paragraph)
        supporting = [bodies[url] for url in urls if url in bodies] if urls else all_bodies
        source_values = {key for body in supporting for _, key in quantities(body)}
        plain = LINK.sub('', paragraph)
        plain = re.sub(r'https?://\S+', '', plain)
        for clause in re.split(r'[。，；\n]', plain):
            forecast = bool(FORECAST.search(clause))
            for match, key in quantities(clause):
                checked += 1
                supported = key in source_values
                if not supported and key == (Decimal(50), '%'):
                    for bound in (r'超过|超出|超|高于', r'不足|低于|小于'):
                        if re.search('(?:' + bound + r')\s*50\s*%', clause):
                            supported = any(re.search('(?:' + bound + r')\s*(?:一半|二分之一)', body) for body in supporting)
                            if supported:
                                break
                if supported and forecast:
                    supported = any(key in {k for _, k in quantities(sentence)} and FORECAST.search(sentence)
                                    for body in supporting for sentence in re.split(r'[。；\n]', body))
                if not supported and not forecast:
                    supported = calculation_supported(clause, key, supporting)
                if supported:
                    continue
                fingerprint = (match[0], clause.strip())
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                issues.append({'issue_type': 'numeric_grounding', 'severity': 'major', 'target_section': '全局',
                    'location': clause.strip()[:240],
                    'description': f'数字依据待核对：{match[0]} 未在本段引用原文中找到相符依据' if urls else f'数字依据待核对：{match[0]} 未在本次检索摘录中找到相符依据',
                    'evidence': '程序仅核对数值、单位与预测标记；未命中不代表现实中不存在该数据。',
                    'suggestion': '补上支持该断言的原文引用，或删去该定量断言、改为有依据的定性分析；计算值需展示原始输入、四则算式与来源。',
                    'requires_new_search': False, 'origin': 'numeric_guard'})
    return {'issues': issues, 'checked_quantities': checked,
            'note': '数字存在性检查不能证明对象、年份或指标正确；需要语义审核。'}


def normalize_review(review, guard_issues=()):
    result = copy.deepcopy(review or {})
    issues = result.setdefault('issues', [])
    known = {(x.get('issue_type'), x.get('location'), x.get('description')) for x in issues}
    for issue in guard_issues:
        key = (issue.get('issue_type'), issue.get('location'), issue.get('description'))
        if key not in known:
            issues.append(copy.deepcopy(issue)); known.add(key)
    for issue in issues:
        if issue.get('issue_type') in {'hallucination', 'unsupported_claim', 'numeric_mismatch', 'numeric_grounding'}:
            if issue.get('severity') != 'critical':
                issue['severity'] = 'major'
    assessment = result.setdefault('overall_assessment', {})
    blocked = any(i.get('severity') in ('major', 'critical') for i in issues)
    if blocked or assessment.get('review_incomplete'):
        assessment['verdict'] = 'needs_revision'
        try: assessment['quality_score'] = min(float(assessment.get('quality_score') or 0), 6)
        except (TypeError, ValueError): assessment['quality_score'] = 0
    return result
