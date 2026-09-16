"""个人研究真实 API 验收。只创建和清理本脚本的随机测试数据。"""
import json
import secrets
import sys
import time
from pathlib import Path
import httpx
from dotenv import load_dotenv

APP = Path(__file__).resolve().parents[1]
load_dotenv(APP.parent / '.env')
sys.path.insert(0, str(APP))
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=100, trust_env=False)
users, kbs, sessions, reports = [], [], [], {}


def check(name, value, detail=None):
    reports[name] = {'passed': bool(value), 'detail': detail}
    (out / 'results.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(('PASS ' if value else 'FAIL ') + name, flush=True)
    assert value, name


def api(method, path, user=0, **kwargs):
    response = client.request(method, path, headers={'Authorization': 'Bearer ' + users[user]['access_token']}, **kwargs)
    response.raise_for_status()
    return response


def new_session(user=0):
    sid = api('POST', '/sessions', user, json={'title': '个人研究回归', 'session_type': 'deepsearch'}).json()['id']
    sessions.append((user, sid))
    return sid


def wait_run(rid, name):
    deadline = time.monotonic() + 450
    while time.monotonic() < deadline:
        result = api('GET', f'/assistant/runs/{rid}').json()
        if result['status'] not in ('queued', 'running'):
            (out / f'{name}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
        time.sleep(2)
    raise TimeoutError(name)


try:
    for _ in range(2):
        tag = secrets.token_hex(5)
        response = client.post('/auth/register', json={'username': 'qa_personal_' + tag, 'email': tag + '@example.com', 'password': secrets.token_urlsafe(22)})
        response.raise_for_status(); users.append(response.json())
    for i in range(2):
        kbs.append(api('POST', '/knowledge-bases', i, json={'name': '回归知识库_' + secrets.token_hex(3)}).json()['id'])
    upload = api('POST', f'/knowledge-bases/{kbs[0]}/documents', files={'file': ('personal_fixture.txt', '合成项目 CedarDesk。内部验证编号 CEDAR-839142。试点共有47个站点。本文为测试材料，不是真实业务数据。'.encode(), 'text/plain')}).json()
    for _ in range(60):
        documents = api('GET', f'/knowledge-bases/{kbs[0]}/documents').json()
        if documents[0]['status'] in ('completed', 'failed'): break
        time.sleep(2)
    check('document_ready', documents[0]['status'] == 'completed')
    memory = api('POST', '/assistant/memories', json={'content': '回答优先使用中文，先给简短结论。'}).json()
    api('PUT', '/assistant/memories/' + memory['id'], json={'content': '回答先给结论，并且指出数据来源。'})
    check('memory_create_edit', any(m['content'] == '回答先给结论，并且指出数据来源。' for m in api('GET', '/assistant/memories').json()))
    check('memory_isolation', not api('GET', '/assistant/memories', 1).json())
    sid = new_session()
    forbidden = client.post('/assistant/runs', headers={'Authorization': 'Bearer ' + users[1]['access_token']}, json={'session_id': sid, 'query': '越权测试'})
    check('foreign_session_rejected', forbidden.status_code == 404)
    rejected = client.post('/assistant/runs', headers={'Authorization': 'Bearer ' + users[0]['access_token']}, json={'session_id': sid, 'query': '越权知识库', 'sources': ['local'], 'kb_ids': [kbs[1]]})
    check('foreign_knowledge_rejected', rejected.status_code == 404)
    run = api('POST', '/assistant/runs', json={'session_id': sid, 'query': '研究 CedarDesk 项目的验证编号和试点站点数量，仅根据知识库，简洁回答。', 'mode': 'research', 'sources': ['local'], 'kb_ids': [kbs[0]]}).json()
    rid = run['id']
    check('create_returns_background_run', run['status'] in ('queued', 'running'))
    concurrent = client.post('/assistant/runs', headers={'Authorization': 'Bearer ' + users[0]['access_token']}, json={'session_id': sid, 'query': '重复启动'})
    check('same_session_concurrency_rejected', concurrent.status_code == 409)
    for path in [f'/assistant/runs/{rid}', f'/assistant/runs/{rid}/events']:
        response = client.get(path, headers={'Authorization': 'Bearer ' + users[1]['access_token']})
        check('run_owner_' + path.rsplit('/', 1)[-1], response.status_code == 404)
    result = wait_run(rid, 'local-research')
    check('local_research_completed', result['status'] in ('completed', 'partial'), result['status'])
    check('local_answer_grounded', 'CEDAR-839142' in result['report'] and '47' in result['report'] and '[E1]' in result['report'])
    check('scope_no_web_or_sql', all(a['tool'] == 'knowledge_search' for a in result['state']['actions']))
    check('explicit_memory_used', memory['id'] in result['state']['memory_ids'])
    check('usage_recorded', result['state']['usage']['model_calls'] >= 2 and result['state']['usage']['prompt_tokens'] > 0, result['state']['usage'])
    events = api('GET', f'/assistant/runs/{rid}/events', params={'after': 2}).text
    check('event_replay_after_cursor', 'id: 1\n' not in events and 'id: 2\n' not in events and 'event: done' in events)
    history = api('GET', f'/sessions/{sid}').json()['messages']
    check('server_saved_messages', [m['role'] for m in history] == ['user', 'assistant'])
    tables = api('GET', '/database/tables').json()
    check('only_business_tables_visible', {t['name'] for t in tables} <= {'company_data', 'industry_stats', 'policy_data'})
    for sql in ['SELECT * FROM users', 'SELECT pg_sleep(1)', 'SELECT * FROM chat_messages']:
        response = client.post('/database/query', headers={'Authorization': 'Bearer ' + users[0]['access_token']}, json={'sql': sql})
        check('sql_rejected_' + sql.split()[-1], response.status_code == 400)
    query = api('POST', '/database/text2sql', json={'question': 'company_data 表中共有多少条记录？'}).json()
    check('text2sql_actual_schema', query['success'] and bool(query['data']), query.get('sql') or query.get('error'))
    sqlsid = new_session()
    sqlrun = api('POST', '/assistant/runs', json={'session_id': sqlsid, 'query': '查询 company_data 表的记录总数，并说明查询口径。', 'mode': 'sql', 'sources': ['database']}).json()
    sqlresult = wait_run(sqlrun['id'], 'sql-research')
    check('sql_research_evidence', sqlresult['status'] in ('completed', 'partial') and any(e['source'] == 'database' and e.get('sql') for e in sqlresult['state']['evidence']))
    api('DELETE', '/assistant/memories/' + memory['id'])
    check('memory_deleted', not api('GET', '/assistant/memories').json())
    websid = new_session()
    webrun = api('POST', '/assistant/runs', json={'session_id': websid, 'query': '检索 PostgreSQL 官方文档，简要说明只读事务的含义，并引用来源。', 'mode': 'research', 'sources': ['web'], 'max_rounds': 1}).json()
    webresult = wait_run(webrun['id'], 'web-research')
    check('web_research_evidence', webresult['status'] in ('completed', 'partial') and bool(webresult['state']['evidence']), webresult['status'])
    check('deleted_memory_not_recalled', memory['id'] not in webresult['state']['memory_ids'])
    cancelsid = new_session()
    cancelrun = api('POST', '/assistant/runs', json={'session_id': cancelsid, 'query': '研究知识管理工具', 'sources': ['web']}).json()
    api('POST', '/assistant/runs/' + cancelrun['id'] + '/cancel')
    time.sleep(2)
    check('cancel_persisted', api('GET', '/assistant/runs/' + cancelrun['id']).json()['status'] == 'cancelled')
finally:
    for user, sid in sessions:
        try: api('DELETE', '/sessions/' + sid, user)
        except Exception: pass
    for user, kb in enumerate(kbs):
        try: api('DELETE', '/knowledge-bases/' + kb, user)
        except Exception: pass
    if users:
        from core.database import SessionLocal
        from models.user import User
        from models.chat import LongTermMemory
        with SessionLocal() as db:
            ids = [u['user']['id'] for u in users]
            db.query(LongTermMemory).filter(LongTermMemory.user_id.in_(ids)).delete(synchronize_session=False)
            db.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
    client.close()
    print('Temporary test data cleaned', flush=True)
