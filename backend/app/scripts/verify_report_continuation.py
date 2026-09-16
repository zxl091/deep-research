"""复用隔离验收任务的检索检查点，另建报告验收；不是从零运行的性能测试。"""
import copy
import json
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4
import httpx
from dotenv import load_dotenv

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]
RUNTIME = Path(r'E:\desktop\简历项目\deep_reasearch\.runtime\industry-assistant')
load_dotenv(APP.parent / '.env')
sys.path.insert(0, str(APP))
import models
from core.database import SessionLocal
from models.assistant import AssistantRun
from models.chat import ChatSession
from models.user import User


def prepare():
    fixture = json.loads((RUNTIME / 'full-research-fixture.json').read_text(encoding='utf-8'))
    with SessionLocal() as db:
        parent = db.get(AssistantRun, UUID(fixture['run_id']))
        user = db.get(User, parent.user_id)
        assert user.username == 'qa_full_f59984064e' and str(user.id) == fixture['user_id']
        assert parent.status == 'failed' and parent.state['usage']['elapsed_seconds'] >= 1800
        sid, rid = uuid4(), uuid4()
        state = copy.deepcopy(parent.state)
        state['validation_origin'] = {'parent_run_id': str(parent.id), 'parent_status': parent.status,
            'parent_usage': copy.deepcopy(state['usage']), 'note': '复用已检索资料和章节草稿的独立报告验收，不代表从零研究耗时'}
        state['usage'] = {'tool_calls': 0, 'model_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'elapsed_seconds': 0}
        state['budget']['max_seconds'] = 1800
        state['next_stage'] = 'charts'
        state['actions'] = []
        state['stages'] = []
        state.pop('error', None)
        state['deep_state']['session_id'] = str(sid)
        db.add(ChatSession(id=sid, user_id=user.id, title='修复后报告验收（复用研究资料）', session_type='deepsearch'))
        db.flush()
        db.add(AssistantRun(id=rid, session_id=sid, user_id=user.id, query=parent.query,
            status='interrupted', state=state, events=[], report=''))
        db.commit()
    fixture.update(session_id=str(sid), run_id=str(rid), parent_run_id=fixture['run_id'])
    (RUNTIME / 'report-continuation-fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
    return fixture


def main():
    fixture = (json.loads((RUNTIME / 'report-continuation-fixture.json').read_text(encoding='utf-8'))
        if '--observe' in sys.argv or '--resume' in sys.argv else prepare())
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=50, trust_env=False,
            headers={'Authorization': 'Bearer ' + fixture['access_token']}) as client:
        rid = fixture['run_id']
        if '--observe' not in sys.argv:
            response = client.post('/assistant/runs/' + rid + '/resume')
            response.raise_for_status()
        print('独立报告验收任务：' + rid, flush=True)
        last = None
        deadline = time.monotonic() + 1850
        while time.monotonic() < deadline:
            response = client.get('/assistant/runs/' + rid)
            response.raise_for_status()
            run = response.json(); state = run['state']
            progress = (run['status'], state.get('next_stage'), len(state.get('stages', [])))
            if progress != last:
                print(json.dumps({'status': run['status'], 'stage': state.get('next_stage'),
                    'charts': len(state.get('charts', [])), 'reviews': len(state.get('review_history', [])),
                    'usage': state['usage']}, ensure_ascii=False), flush=True)
                last = progress
            if run['status'] not in ('running', 'queued'):
                (ROOT / 'docs/report-continuation-live-result.json').write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding='utf-8')
                (ROOT / 'docs/电动汽车行业研究-修复后验收报告.md').write_text(run['report'] or state.get('error', '未生成报告'), encoding='utf-8')
                print('报告验收终态：' + run['status'], flush=True)
                return
            time.sleep(3)
        response = client.post('/assistant/runs/' + rid + '/cancel')
        response.raise_for_status()
        raise TimeoutError('报告验收超过独立预算')


if __name__ == '__main__': main()
