import asyncio
import json
import hashlib
import httpx
from dataclasses import dataclass
from config.llm_config import get_config
from core.database import SessionLocal
from service.knowledge_index import search_user_knowledge


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    source: str
    timeout: int


TOOLS = {
    'knowledge_search': ToolSpec('knowledge_search', '检索个人知识库', 'local', 60),
    'web_search': ToolSpec('web_search', '搜索公开网页', 'web', 40),
    'sql_query': ToolSpec('sql_query', '查询授权业务数据', 'database', 100),
}


def evidence_key(item):
    return hashlib.sha256((item['url'] + '\n' + item['content']).encode()).hexdigest()


async def execute_tool(name, query, scope, user_id, model):
    spec = TOOLS.get(name)
    if not spec or spec.source not in scope['sources']:
        raise ValueError('工具不在用户允许的资料范围内')
    if name == 'knowledge_search':
        hits = await asyncio.to_thread(search_user_knowledge, query, user_id, scope['kb_ids'], 5)
        return [{'title': h['filename'], 'url': h['url'], 'content': h['content'][:5000],
                 'source': 'knowledge', 'doc_id': h['doc_id']} for h in hits]
    if name == 'web_search':
        key = get_config().search_api_key
        if not key:
            raise ValueError('联网搜索尚未配置')
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post('https://api.bocha.cn/v1/web-search',
                headers={'Authorization': f'Bearer {key}'}, json={'query': query, 'summary': True, 'count': 5})
            response.raise_for_status()
            data = response.json()
        if data.get('code') != 200:
            raise ValueError('联网搜索服务未返回成功结果')
        return [{'title': h.get('name', '网页'), 'url': h['url'],
                 'content': (h.get('summary') or h.get('snippet') or '')[:5000], 'source': 'web'}
                for h in data.get('data', {}).get('webPages', {}).get('value', [])
                if h.get('url', '').startswith(('https://', 'http://')) and (h.get('summary') or h.get('snippet'))]
    from service.text2sql_service import query_business_data
    result = await query_business_data(query, model)
    return [{'title': '业务数据库查询', 'url': 'sql://' + hashlib.sha256(result['sql'].encode()).hexdigest()[:20],
             'content': json.dumps(result, ensure_ascii=False, default=str)[:12000], 'source': 'database',
             'sql': result['sql'], 'columns': result['columns'], 'rows': result['data']}]
