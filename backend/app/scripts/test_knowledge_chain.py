"""真实服务链路回归。使用合成数据；会调用已配置的文档解析、向量和模型服务。

运行：python app/scripts/test_knowledge_chain.py setup|research|cleanup --output-dir PATH
setup 创建隔离测试账号；research 运行一次本地研究；cleanup 删除测试知识库和账号。
凭据仅保存在 output-dir 中，不写入报告或标准输出。
"""
import argparse
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


def pdf_fixture(text):
    stream = f'BT /F1 11 Tf 40 760 Td ({text}) Tj ET'.encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 900 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    result = b'%PDF-1.4\n'
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{number} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref = len(result)
    result += b'xref\n0 6\n0000000000 65535 f \n'
    result += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return result + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['setup', 'research', 'race', 'cleanup'])
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / 'credentials.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    report_path = out / 'results.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=180, trust_env=False)

    def save():
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def check(name, condition, detail=None):
        report[name] = {'passed': bool(condition), 'detail': detail}
        save()
        print(('PASS ' if condition else 'FAIL ') + name, detail or '', flush=True)
        assert condition, name

    def headers(user):
        return {'Authorization': 'Bearer '+user['token']}

    def api(method, path, user=None, **kwargs):
        response = client.request(method, path, headers=headers(user) if user else {}, **kwargs)
        response.raise_for_status()
        return response

    def register():
        tag = secrets.token_hex(5)
        account = {'username': 'qa_chain_'+tag, 'email': 'qa_chain_'+tag+'@example.com', 'password': secrets.token_urlsafe(20)}
        auth = api('POST', '/auth/register', json=account).json()
        return {**account, 'token': auth['access_token'], 'id': auth['user']['id']}

    def upload(user, kb, content, filename='资料.pdf'):
        started = time.monotonic()
        data = api('POST', f'/knowledge-bases/{kb}/documents', user,
                   files={'file': (filename, content, 'application/pdf' if filename.endswith('.pdf') else 'text/plain')}).json()
        state.setdefault('documents', []).append({'kb': kb, 'id': data['id']})
        save()
        return data['id'], time.monotonic()-started

    def wait_doc(user, kb, did):
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            docs = api('GET', f'/knowledge-bases/{kb}/documents', user).json()
            doc = next(d for d in docs if d['id'] == did)
            if doc['status'] in ('completed', 'failed'):
                check('document_ready_'+did, doc['status']=='completed', {'status':doc['status'],'chunks':doc['chunk_count'],'error':doc['error_message']})
                return doc
            time.sleep(2)
        raise TimeoutError('document processing')

    def chat(user, question, **extra):
        response = api('POST', '/chat/completion/v3' if extra.get('attachment_ids') else '/chat/completion', user,
                       json={'question': question, 'search_web': False, 'search_knowledge': True, **extra})
        events=[]
        for line in response.text.splitlines():
            if line.startswith('data: ') and line[6:] != '[DONE]':
                events.append(json.loads(line[6:]))
        assert not any(e.get('role')=='error' for e in events), events
        answer=''.join(e.get('content','') for e in events if e.get('role')=='assistant' and not e.get('thinking'))
        docs=next((e['documents'] for e in events if 'documents' in e),[])
        return answer, docs

    if args.phase == 'setup':
        assert not state.get('users'), 'Use a fresh output directory for setup.'
        state['users'] = [register(), register()]
        save()
        a,b=state['users']
        kb_name='中文知识库_'+secrets.token_hex(3)
        state['kbs']=[api('POST','/knowledge-bases',u,json={'name':kb_name}).json()['id'] for u in [a,b]]
        save()
        ka,kb=state['kbs']
        data=pdf_fixture('Synthetic AsterRelay project. Validation code: ASTER-7319426. Pilot: 47 junctions.')
        (out/'synthetic.pdf').write_bytes(data)
        did, elapsed=upload(a,ka,data)
        state['main_doc']=did
        save()
        check('upload_returns_without_waiting_for_parser',elapsed<5,round(elapsed,2))
        t=time.monotonic()
        api('GET','/hello')
        check('api_responsive_during_parsing',time.monotonic()-t<3)
        wait_doc(a,ka,did)
        chunks=api('GET',f'/knowledge-bases/{ka}/documents/{did}/chunks',a).json()
        check('pdf_body_extracted','ASTER-7319426' in json.dumps(chunks))
        other,_=upload(a,ka,pdf_fixture('Synthetic BirchTransit project. Validation code: BIRCH-1188332.'))
        state['same_name_doc']=other
        save()
        wait_doc(a,ka,other)
        other_chunks=api('GET',f'/knowledge-bases/{ka}/documents/{other}/chunks',a).json()
        check('same_filename_isolated','BIRCH-1188332' in json.dumps(other_chunks) and 'ASTER-7319426' not in json.dumps(other_chunks))
        other_user_doc,_=upload(b,kb,b'Synthetic AsterRelay other-user secret: OTHER-9826115.', 'private.txt')
        wait_doc(b,kb,other_user_doc)
        api('PUT',f'/knowledge-bases/{ka}',a,json={'name':kb_name+'_已改名'})
        check('rename_preserves_chunks','ASTER-7319426' in json.dumps(api('GET',f'/knowledge-bases/{ka}/documents/{did}/chunks',a).json()))
        for path, body in [('/chat/completion',{'question':'AsterRelay','kb_ids':[ka],'search_web':False}),
                           ('/research/stream',{'query':'AsterRelay','kb_ids':[ka],'search_modes':['local']})]:
            r=client.post(path,headers=headers(b),json=body)
            check('cross_user_rejected_'+path,r.status_code==404,r.status_code)
        answer,docs=chat(a,'仅根据文档，AsterRelay 的验证代码和试点路口数量是什么？简短回答。',kb_ids=[ka])
        (out/'chat-answer.json').write_text(json.dumps({'answer':answer,'documents':docs},ensure_ascii=False,indent=2),encoding='utf-8')
        check('chat_uses_uploaded_document','ASTER-7319426' in answer and '47' in answer and any(d.get('document_id')==did for d in docs))
        check('other_user_content_not_retrieved','OTHER-9826115' not in json.dumps(docs))
        sid=api('POST','/sessions',a,json={'title':'文档链路回归','session_type':'chat'}).json()['id']
        state['attachment_session']=sid
        att=api('POST','/attachments',a,data={'session_id':sid},files={'file':('附件.pdf',data,'application/pdf')}).json()
        state['attachment_id']=att['id']
        save()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            att=api('GET',f"/attachments/{att['id']}",a).json()
            if att['status'] in ['completed','failed']: break
            time.sleep(2)
        check('pdf_attachment_ready',att['status']=='completed',att['status'])
        check('other_user_attachment_rejected',client.get(f"/attachments/{att['id']}",headers=headers(b)).status_code==404)
        answer,_=chat(a,'只根据附件回答 AsterRelay 验证代码。',session_id=sid,attachment_ids=[att['id']],search_knowledge=False)
        check('pdf_attachment_answer','ASTER-7319426' in answer)
        state['research_session']=api('POST','/sessions',a,json={'title':'本地知识库研究回归','session_type':'deepsearch'}).json()['id']
        save()
        print('Setup complete; ready for research phase.',flush=True)

    elif args.phase == 'research':
        a,b=state['users']; ka=state['kbs'][0]
        payload={'query':'仅根据本地文档，核对合成项目 AsterRelay 的验证代码和试点路口数量，给出包含原始文档引用的简短核验报告。资料没有的数据明确写未知。不要预测，不需要图表，不使用互联网。',
                 'session_id':state['research_session'],'search_modes':['local'],'kb_ids':[ka],'version':'v2','max_iterations':1}
        started=time.monotonic(); completion=None; local_hit=False; errors=[]
        with (out/'research-events.jsonl').open('w',encoding='utf-8') as log:
            with client.stream('POST','/research/stream',headers=headers(a),json=payload,timeout=httpx.Timeout(1800,read=240)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith('data: ') or line[6:]=='[DONE]': continue
                    event=json.loads(line[6:]); log.write(json.dumps(event,ensure_ascii=False)+'\n');log.flush()
                    if event.get('type')=='phase': print('RESEARCH',event.get('phase'),flush=True)
                    if 'local://kb/'+ka in json.dumps(event): local_hit=True
                    if event.get('type')=='error': errors.append(event)
                    if event.get('type')=='research_complete': completion=event
        check('research_completed',completion is not None and not errors,{'seconds':round(time.monotonic()-started,2),'errors':errors})
        check('research_received_local_sources',local_hit)
        text=json.dumps(completion,ensure_ascii=False)
        check('research_answer_and_reference','ASTER-7319426' in text and '47' in text and 'local://kb/'+ka in text)
        check('research_respects_no_charts', completion.get('charts_count') == 0)
        check('local_references_labelled_correctly', any(r.get('source') == 'knowledge' and r.get('link','').startswith('local://kb/'+ka) for r in completion.get('references',[])))
        checkpoint=api('GET',f"/research/checkpoint/{state['research_session']}/full",a).json()
        (out/'research-checkpoint.json').write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2),encoding='utf-8')
        saved=checkpoint.get('checkpoint',{})
        check('final_checkpoint_persisted', checkpoint.get('success') and saved.get('status') == 'completed' and saved.get('final_report') == completion['final_report'] and saved.get('state_json',{}).get('kb_ids') == [ka])
        r=client.get(f"/research/checkpoint/{state['research_session']}/full",headers=headers(b))
        check('research_checkpoint_isolation',r.status_code==404)
        (out/'research-completion.json').write_text(json.dumps(completion,ensure_ascii=False,indent=2),encoding='utf-8')

    elif args.phase == 'race':
        from core.database import SessionLocal
        from models.knowledge import Document
        from service.knowledge_index import collection_for_kb
        from service.milvus_service import get_milvus_service
        from uuid import UUID
        a,b=state['users']
        race_kb=api('POST','/knowledge-bases',a,json={'name':'删除并发回归_'+secrets.token_hex(4)}).json()['id']
        did,_=upload(a,race_kb,(out/'synthetic.pdf').read_bytes())
        with SessionLocal() as db:
            document=db.query(Document).filter(Document.id==UUID(did)).one()
            temp=Path(document.file_path)
        api('DELETE',f'/knowledge-bases/{race_kb}',a)
        deadline=time.monotonic()+180
        while temp.exists() and time.monotonic()<deadline:
            time.sleep(2)
        check('delete_during_processing', not temp.exists() and not get_milvus_service().get_collection_stats(collection_for_kb(race_kb))['exists'])
        response=client.post('/attachments',headers=headers(b),data={'session_id':state['attachment_session']},
                             files={'file':('test.txt',b'ownership test','text/plain')})
        check('cross_user_attachment_upload',response.status_code==404,response.status_code)

    else:
        from service.knowledge_index import collection_for_kb, search_user_knowledge
        from service.milvus_service import get_milvus_service
        from core.database import SessionLocal
        from models.user import User
        from models.chat import ChatSession
        from models.research import ResearchCheckpoint
        from service.session_service import SessionService
        from uuid import UUID
        a,b=state['users']; ka,kb=state['kbs']
        milvus=get_milvus_service()
        api('DELETE',f"/knowledge-bases/{ka}/documents/{state['main_doc']}",a)
        check('deleted_document_vectors_removed',not milvus.get_chunks_by_doc_id(collection_for_kb(ka),state['main_doc']))
        check('same_filename_other_document_preserved',bool(milvus.get_chunks_by_doc_id(collection_for_kb(ka),state['same_name_doc'])))
        hits=search_user_knowledge('AsterRelay',a['id'],[ka])
        check('deleted_document_not_retrieved',all(hit['doc_id']!=state['main_doc'] for hit in hits))
        api('DELETE',f'/knowledge-bases/{ka}',a)
        check('deleted_knowledge_collection_removed',not milvus.get_collection_stats(collection_for_kb(ka))['exists'])
        answer,docs=chat(a,'AsterRelay 验证代码是什么？')
        check('empty_kb_no_fabricated_answer',not docs and 'ASTER-7319426' not in answer and '没有找到' in answer)
        api('DELETE',f'/knowledge-bases/{kb}',b)
        api('DELETE',f"/attachments/{state['attachment_id']}",a)
        with SessionLocal() as db:
            ids=[UUID(u['id']) for u in state['users']]
            db.query(ResearchCheckpoint).filter(ResearchCheckpoint.user_id.in_(ids)).delete(synchronize_session=False)
            cache=SessionService().redis_client
            for session in db.query(ChatSession).filter(ChatSession.user_id.in_(ids)).all():
                keys=list(cache.scan_iter(match=f'message:{session.id}:*'))
                keys.extend([f'session:{session.id}',f'session:{session.id}:messages',f'research:cancel:{session.id}'])
                cache.delete(*keys)
                db.delete(session)
            db.flush()
            for user in db.query(User).filter(User.id.in_(ids)).all(): db.delete(user)
            db.commit()
        state['cleaned']=True;save()
        print('Test accounts and test data cleaned.',flush=True)


if __name__ == '__main__':
    main()
