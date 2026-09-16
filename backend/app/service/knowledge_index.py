"""知识库索引约定及按用户授权的检索入口。"""
from uuid import UUID
from fastapi import HTTPException
from core.database import SessionLocal
from models.knowledge import KnowledgeBase, Document


def collection_for_kb(kb_id: str) -> str:
    return f"kb_{UUID(str(kb_id)).hex}"


def resolve_knowledge_ids(db, user_id, kb_ids=None, kb_name=None):
    if not user_id:
        raise HTTPException(401, "使用本地知识库需要登录")
    query = db.query(KnowledgeBase).filter(KnowledgeBase.user_id == user_id)
    if kb_ids is not None:
        try:
            selected = list(dict.fromkeys(UUID(str(k)) for k in kb_ids))
        except ValueError:
            raise HTTPException(400, "无效的知识库ID")
        kbs = query.filter(KnowledgeBase.id.in_(selected)).all()
        if len(kbs) != len(selected):
            raise HTTPException(404, "知识库不存在或无权访问")
    elif kb_name:
        kbs = query.filter(KnowledgeBase.name == kb_name).all()
        if not kbs:
            raise HTTPException(404, "知识库不存在或无权访问")
    else:
        kbs = query.all()
    return [str(kb.id) for kb in kbs]


def search_user_knowledge(question, user_id, kb_ids=None, top_k=5):
    """每次检索重新核验归属和存活文档，已删除或处理失败的文档不可召回。"""
    from service.embedding_service import generate_embedding
    from service.milvus_service import get_milvus_service
    with SessionLocal() as db:
        ids = resolve_knowledge_ids(db, user_id, kb_ids)
        documents = db.query(Document).filter(
            Document.user_id == user_id,
            Document.knowledge_base_id.in_([UUID(k) for k in ids]),
            Document.status == "completed",
        ).all()
        grouped = {}
        for doc in documents:
            grouped.setdefault(str(doc.knowledge_base_id), []).append(str(doc.id))
    if not grouped:
        return []
    vector = generate_embedding(question)
    if not vector:
        raise RuntimeError("知识库查询向量生成失败，请检查向量服务连接")
    milvus = get_milvus_service()
    hits = []
    for kb_id, doc_ids in grouped.items():
        hits.extend(milvus.search(collection_for_kb(kb_id), vector, top_k,
                                  kb_id=kb_id, doc_ids=doc_ids))
    hits.sort(key=lambda hit: hit.get("score", 0), reverse=True)
    for hit in hits[:top_k]:
        hit["url"] = f"local://kb/{hit['kb_id']}/{hit['doc_id']}"
    return hits[:top_k]


def chat_knowledge_documents(question, user_id, kb_ids=None):
    return [{
        "id": i, "document_id": hit["doc_id"], "document_name": hit["filename"],
        "content": hit["content"], "content_with_weight": hit["content"],
        "source": "knowledge", "title": hit["filename"], "link": hit["url"],
        "kb_id": hit["kb_id"], "weight": hit["score"],
    } for i, hit in enumerate(search_user_knowledge(question, user_id, kb_ids), 1)]
