"""真实完整研究验收。测试身份单独保存到本地运行目录，结果不含凭据。"""
import json
import secrets
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path(r'E:\desktop\简历项目\deep_reasearch\.runtime\industry-assistant')
client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=50, trust_env=False)
if '--resume' in sys.argv:
    fixture=json.loads((RUNTIME/'full-research-fixture.json').read_text(encoding='utf-8'))
    sid,rid=fixture['session_id'],fixture['run_id']
    client.headers['Authorization']='Bearer '+fixture['access_token']
    response=client.get('/assistant/runs/'+rid);response.raise_for_status()
    assert response.json()['status'] in ('interrupted', 'failed'), response.json()['status']
    response=client.post('/assistant/runs/'+rid+'/resume');response.raise_for_status()
    print('Checkpoint resume verified',flush=True)
else:
    name = 'qa_full_' + secrets.token_hex(5)
    password = secrets.token_urlsafe(24)
    response = client.post('/auth/register', json={'username':name,'email':name+'@example.com','password':password})
    response.raise_for_status(); account=response.json()
    client.headers['Authorization'] = 'Bearer ' + account['access_token']
    response=client.post('/sessions',json={'title':'完整研究验收：电动汽车行业','session_type':'deepsearch'})
    response.raise_for_status(); sid=response.json()['id']
    response=client.post('/assistant/runs',json={'session_id':sid,'query':'研究下近几年电动汽车行业的发展','mode':'research','sources':['web'],'use_memory':False})
    response.raise_for_status(); rid=response.json()['id']
    (RUNTIME/'full-research-fixture.json').write_text(json.dumps({'username':name,'password':password,'user_id':account['user']['id'],'access_token':account['access_token'],'session_id':sid,'run_id':rid}),encoding='utf-8')
    print('Full research started: '+rid,flush=True)
deadline=time.monotonic()+1850
last=None
while time.monotonic()<deadline:
    response=client.get('/assistant/runs/'+rid);response.raise_for_status();run=response.json()
    state=run['state']; progress=(run['status'],state.get('next_stage'),len(state.get('stages',[])),len(state.get('draft_sections',{})))
    if progress != last:
        print(json.dumps({'status':run['status'],'stage':state.get('next_stage'),'sections':len(state.get('outline',[])),'drafts':len(state.get('draft_sections',{})),'evidence':len(state.get('evidence',[])),'usage':state['usage']},ensure_ascii=False),flush=True);last=progress
    if run['status'] not in ('running','queued'):
        (ROOT/'docs/full-research-live-result.json').write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding='utf-8')
        (ROOT/'docs/电动汽车行业完整研究-验收样本.md').write_text(run['report'] or state.get('error','未生成报告'),encoding='utf-8')
        print('Finished '+run['status'],flush=True)
        break
    time.sleep(3)
else:
    client.post('/assistant/runs/'+rid+'/cancel')
    raise TimeoutError('Full research verification exceeded deadline')
client.close()
