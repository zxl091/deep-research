"""真实 API + 配置模型的业务数据验收；只清理本脚本创建的临时账户。"""
import json
import secrets
import sys
import time
from pathlib import Path
import httpx
from seed_business_demo import TAG, fixtures, seed
from core.database import SessionLocal
from models.user import User

OUTPUT = Path(__file__).resolve().parents[3] / 'docs' / 'business-demo-results.json'


def normalized(rows):
    # SQL 别名不影响正确性，比较每行值与行集合；允许数值序列化为字符串。
    def cell(value):
        try:
            return ('number', round(float(value), 4))
        except (ValueError, TypeError):
            return ('text', str(value))
    return sorted([sorted(cell(v) for v in row.values()) for row in rows])


def main():
    results = {'dataset': TAG, 'notice': '全部数据为虚构演示数据', 'checks': []}
    def check(name, passed, **detail):
        results['checks'].append({'name': name, 'passed': bool(passed), **detail})
        OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
    check('seed_is_idempotent', all(n == 0 for n in seed().values()))
    client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=110, trust_env=False)
    user = None
    session_ids = []
    try:
        tag = secrets.token_hex(6)
        response = client.post('/auth/register', json={'username': 'qa_business_' + tag, 'email': tag + '@example.com', 'password': secrets.token_urlsafe(24)})
        response.raise_for_status()
        account = response.json()
        user = account['user']['id']
        client.headers['Authorization'] = 'Bearer ' + account['access_token']
        tables = client.get('/database/tables').json()
        for table, expected in [('company_data', 18), ('industry_stats', 18), ('policy_data', 6)]:
            detail = client.get(f'/database/tables/{table}/data', params={'limit': 5, 'offset': 0})
            check('browse_' + table, detail.status_code == 200 and len(detail.json().get('rows', [])) == 5 and next(t['row_count'] for t in tables if t['name'] == table) >= expected)
        cases = [
            ('企业数量', "仅查询 data_source 以 DEMO_BUSINESS_V1 开头的演示数据，2025 年共有多少家企业？只返回数量。", "SELECT COUNT(*) FROM company_data WHERE data_source LIKE 'DEMO_BUSINESS_V1%' AND year=2025"),
            ('营收排名', "仅查询 data_source 以 DEMO_BUSINESS_V1 开头的演示数据，2025 年营收最高的三家企业，返回企业名称和营收，按营收降序。", "SELECT company_name, revenue FROM company_data WHERE data_source LIKE 'DEMO_BUSINESS_V1%' AND year=2025 ORDER BY revenue DESC LIMIT 3"),
            ('行业分组汇总', "仅查询 data_source 以 DEMO_BUSINESS_V1 开头的演示数据，按行业汇总 2025 年企业营收，返回行业和营收合计。", "SELECT industry, SUM(revenue) FROM company_data WHERE data_source LIKE 'DEMO_BUSINESS_V1%' AND year=2025 GROUP BY industry"),
            ('行业三年趋势', "查询 industry_stats 中 source 为 DEMO_BUSINESS_V1 的智慧交通数据，指标为样本企业营收合计，返回2023到2025年的年份和指标值。", "SELECT year, metric_value FROM industry_stats WHERE source='DEMO_BUSINESS_V1' AND industry_name='智慧交通' AND metric_name='样本企业营收合计' AND year BETWEEN 2023 AND 2025 ORDER BY year"),
            ('年度同比', "仅查询 data_source 以 DEMO_BUSINESS_V1 开头的演示企业数据，智慧交通企业2025年营收合计相对2024年的同比增长率是多少？只返回增长率的百分数数值，不要附加其他列。", "SELECT (SUM(CASE WHEN year=2025 THEN revenue ELSE 0 END)-SUM(CASE WHEN year=2024 THEN revenue ELSE 0 END))*100.0/NULLIF(SUM(CASE WHEN year=2024 THEN revenue ELSE 0 END),0) AS growth FROM company_data WHERE data_source LIKE 'DEMO_BUSINESS_V1%' AND industry='智慧交通' AND year IN (2024,2025)"),
            ('政策日期筛选', "查询政策名称以【虚构演示】开头，2025年发布的政策，返回政策名称和所属行业。", "SELECT policy_name, industry FROM policy_data WHERE policy_name LIKE '【虚构演示】%' AND publish_date>='2025-01-01' AND publish_date<'2026-01-01'"),
            ('无数据不编造', "仅查询 data_source 以 DEMO_BUSINESS_V1 开头的演示数据，返回2030年的企业名称和营收。", "SELECT company_name, revenue FROM company_data WHERE data_source LIKE 'DEMO_BUSINESS_V1%' AND year=2030"),
        ]
        for name, question, reference in cases:
            baseline = client.post('/database/query', json={'sql': reference})
            baseline.raise_for_status()
            started = time.monotonic()
            response = client.post('/database/text2sql', json={'question': question})
            response.raise_for_status()
            generated = response.json()
            check('text2sql_' + name, generated.get('success') and normalized(generated.get('data', [])) == normalized(baseline.json()['rows']), question=question, expected=baseline.json()['rows'], actual=generated, seconds=round(time.monotonic()-started, 2))
        for name, sql in [('写入拒绝', 'DELETE FROM company_data'), ('系统用户表拒绝', 'SELECT * FROM users')]:
            response = client.post('/database/query', json={'sql': sql})
            check(name, response.status_code == 400)
        response = client.post('/sessions', json={'title': '业务数据验收（临时）', 'session_type': 'deepsearch'})
        response.raise_for_status()
        sid = response.json()['id']; session_ids.append(sid)
        response = client.post('/assistant/runs', json={'session_id': sid, 'query': '仅查询 data_source 以 DEMO_BUSINESS_V1 开头的虚构演示企业数据，比较智慧交通行业2024年和2025年的全年营收合计，并计算同比。引用数据库证据，注明单位是亿元和数据为虚构演示，不推测增长原因。', 'mode': 'research', 'sources': ['database'], 'use_memory': False, 'max_rounds': 2})
        response.raise_for_status(); rid = response.json()['id']
        deadline = time.monotonic() + 430
        while time.monotonic() < deadline:
            response = client.get('/assistant/runs/' + rid); response.raise_for_status()
            run = response.json()
            if run['status'] not in ('running', 'queued'):
                break
            time.sleep(2)
        results['research'] = run
        check('research_with_sql_evidence', run['status'] in ('completed', 'partial') and any(e.get('sql') for e in run['state']['evidence']) and '[E' in run.get('report', ''))
        check('research_numbers_and_demo_label', all(v in run.get('report', '') for v in ['40', '50', '25', '演示']))
        check('research_uses_only_database', bool(run['state']['actions']) and all(a['tool'] == 'sql_query' for a in run['state']['actions']))
    finally:
        for sid in session_ids:
            client.delete('/sessions/' + sid)
        if user:
            with SessionLocal() as db:
                db.query(User).filter(User.id == user).delete(synchronize_session=False)
                db.commit()
        client.close()
    return 0 if all(c['passed'] for c in results['checks']) else 1


if __name__ == '__main__':
    sys.exit(main())
