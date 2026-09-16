"""限定 SQL 子集，不声称是完整 SQL 解析器。执行计划再次核验实际关系。"""
import re
from sqlalchemy import text

BUSINESS_TABLES = frozenset({'industry_stats', 'company_data', 'policy_data'})
FUNCTIONS = frozenset({'count', 'sum', 'avg', 'min', 'max', 'round', 'coalesce', 'nullif', 'abs', 'lower', 'upper', 'date_trunc', 'extract', 'cast', 'to_char'})


def validate_sql(sql):
    if not isinstance(sql, str) or len(sql) > 8000:
        raise ValueError('SQL 为空或超过长度限制')
    sql = sql.strip().rstrip(';').strip()
    if not re.match(r'^select\s', sql, re.I):
        raise ValueError('当前仅支持 SELECT 查询')
    if re.search(r';|--|/\*|\*/|\$|\\', sql):
        raise ValueError('禁止多语句、注释或特殊转义')
    clean = re.sub(r"'(?:''|[^'])*'", "''", sql)
    clean = re.sub(r'"([A-Za-z_][A-Za-z_0-9]*)"', r'\1', clean)
    if '"' in clean or re.search(r'[^\w\s.,()*+/%<>=!:\-\']', clean, re.ASCII):
        raise ValueError('SQL 含不支持的语法')
    if re.search(r'\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|into|with|for|lock|set|union|intersect|except|pg_catalog|information_schema)\b', clean, re.I):
        raise ValueError('SQL 超出只读查询子集')
    if '::' in clean:
        raise ValueError('请使用 CAST 代替类型强制转换')
    relations_sql = re.sub(r'\bextract\s*\([^()]*\)', '', clean, flags=re.I)
    for relation in re.findall(r'\b(?:from|join)\s+((?:[a-z_]\w*\.)?[a-z_]\w*)', relations_sql, re.I):
        if relation.lower().removeprefix('public.') not in BUSINESS_TABLES:
            raise ValueError('查询包含未授权的数据表')
    for function in re.findall(r'\b([a-z_]\w*)\s*\(', clean, re.I):
        if function.lower() not in FUNCTIONS | {'in', 'select', 'from', 'as', 'over'}:
            raise ValueError(f'不支持函数：{function}')
    return sql


def check_plan(node):
    if isinstance(node, list):
        for item in node:
            check_plan(item)
    elif isinstance(node, dict):
        if 'Relation Name' in node and (node['Relation Name'] not in BUSINESS_TABLES or node.get('Schema', 'public') != 'public'):
            raise ValueError('查询包含未授权的数据表')
        for value in node.values():
            if isinstance(value, (dict, list)):
                check_plan(value)


def execute_readonly(db, sql, limit=100):
    sql = validate_sql(sql)
    limit = max(1, min(int(limit), 100))
    # 使用独立事务，避免认证 ORM 查询已开启事务时无法设置只读。
    with db.get_bind().connect() as conn:
        with conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text("SET LOCAL statement_timeout = '8s'"))
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            conn.execute(text('SET LOCAL search_path TO public, pg_catalog'))
            plan = conn.execute(text('EXPLAIN (FORMAT JSON, VERBOSE TRUE) ' + sql)).scalar()
            check_plan(plan)
            result = conn.execute(text(f'SELECT * FROM ({sql}) AS research_result LIMIT :result_limit'), {'result_limit': limit})
            columns = list(result.keys())
            rows = [{key: (value.isoformat() if hasattr(value, 'isoformat') else str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value)
                     for key, value in row.items()} for row in result.mappings()]
            return {'columns': columns, 'rows': rows, 'row_count': len(rows)}
