"""共享 Text2SQL：实际授权 Schema、统一模型和受限只读执行。"""
import asyncio
import re
from sqlalchemy.exc import DBAPIError
from core.database import SessionLocal
from service.database_explorer import DatabaseExplorer
from service.assistant.llm import ModelGateway


def load_schema():
    with SessionLocal() as db:
        explorer = DatabaseExplorer(db)
        return [explorer.get_table_schema(t['name']) for t in explorer.get_tables()]


async def query_business_data(question, model=None):
    import json
    model = model or ModelGateway()
    schema = await asyncio.to_thread(load_schema)
    instruction = (
        '根据实际表结构生成 PostgreSQL SELECT。仅查询提供的表；不使用 CTE、UNION、系统表或写入语句。'
        '函数仅允许 count/sum/avg/min/max/round/coalesce/nullif/abs/lower/upper/date_trunc/extract/cast/to_char。'
        '使用英文字母下划线组成的列别名。禁止 :: 类型转换，必须使用 CAST(x AS numeric)。'
        'double precision 的结果若需保留两位小数，应使用 round(CAST(表达式 AS numeric), 2)。'
        '筛选值须逐字保留问题明确指定的文字和标点，包括【】和（）；不得擅自删除括号或改写值。'
        '列的 comment 说明字段含义和单位；year 是年份，quarter 为空的记录代表全年。'
        '无匹配字段时不要编造。返回JSON {"sql":"", "explanation":""}。查询结果最多100行。'
    )
    # 【】作为名称的组成部分时，模型容易将其当作引号丢弃。
    # 仅校验明确的“以【...】开头”条件，不尝试从一般自然语言猜测筛选值。
    required_prefixes = re.findall(r'以\s*(【[^】]+】)\s*开头', question)
    prompt = {'question': question, 'schema': schema, 'required_literal_prefixes': required_prefixes}
    def execute(sql):
        with SessionLocal() as db:
            return DatabaseExplorer(db).execute_query(sql)
    for attempt in range(2):
        result = await model.complete(instruction, json.dumps(prompt, ensure_ascii=False, default=str), True, 1500)
        sql = result.get('sql', '')
        if not sql:
            raise ValueError(result.get('explanation') or '现有表结构无法回答此问题')
        try:
            literals = [value.replace("''", "'") for value in re.findall(r"'((?:''|[^'])*)'", sql)]
            if any(not any(value.startswith(prefix) for value in literals) for prefix in required_prefixes):
                raise ValueError('筛选条件丢失了名称标点。SQL 字符串必须完整保留以下前缀（包括【和】）：' + '、'.join(required_prefixes))
            data = await asyncio.to_thread(execute, sql)
            break
        except (DBAPIError, ValueError) as exc:
            if attempt:
                raise ValueError('SQL 生成后仍未通过只读校验或执行，请明确字段、条件后重试。') from exc
            # 只反馈 SQL 错误主消息，不包含连接参数和完整驱动异常。
            if isinstance(exc, DBAPIError):
                error = getattr(getattr(exc.orig, 'diag', None), 'message_primary', None) or type(exc.orig).__name__
            else:
                error = str(exc)
            prompt['correction'] = {'previous_sql': sql, 'error': error, 'instruction': '修正此错误，保留原问题的条件和查询口径。仍必须遵守全部只读限制。'}
    return {'success': True, 'sql': sql, 'explanation': str(result.get('explanation', '')),
            'columns': data['columns'], 'data': data['rows'], 'row_count': data['row_count'],
            'visualization_hint': 'table', 'data_note': '当前连接为项目业务演示数据，请核验其来源后用于真实决策。'}


class Text2SQLService:
    # 保留旧调用签名，模型统一由 ModelGateway 配置。
    def __init__(self, llm_api_key=None, llm_base_url=None, db_connection_string=None, model=None):
        pass

    def validate_sql(self, sql):
        from service.assistant.sql_policy import validate_sql
        try:
            validate_sql(sql)
            return True, ''
        except ValueError as exc:
            return False, str(exc)

    async def query(self, question, intent='stats'):
        try:
            return await query_business_data(question)
        except Exception as exc:
            return {'success': False, 'error': str(exc)}


def create_text2sql_service(*args, **kwargs):
    return Text2SQLService(*args, **kwargs)
