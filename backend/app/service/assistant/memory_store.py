"""近期窗口缓存和长期摘要向量索引。PostgreSQL 始终是真实来源。"""
import hashlib
import json
import os
from uuid import UUID
import redis
from pymilvus import Collection, CollectionSchema, FieldSchema, DataType, utility
from service.embedding_service import generate_embedding
from service.milvus_service import get_milvus_service

COLLECTION = 'research_summary_memory_v1'


def cache_client():
    return redis.Redis(host=os.getenv('REDIS_HOST', 'localhost'), port=int(os.getenv('REDIS_PORT', 6379)),
                       password=os.getenv('REDIS_PASSWORD') or None, decode_responses=True,
                       socket_connect_timeout=1, socket_timeout=1)


def cache_key(uid, sid):
    return f'research:window:v1:{UUID(str(uid))}:{UUID(str(sid))}'


def read_window(uid, sid, last_id):
    client = cache_client(); key = cache_key(uid, sid)
    if client.get(key + ':head') != last_id:
        return None
    values = [json.loads(item) for item in client.lrange(key, 0, -1)]
    return values if values and values[-1]['id'] == last_id else None


def write_window(uid, sid, messages):
    if not messages:
        return
    key = cache_key(uid, sid)
    with cache_client().pipeline(transaction=True) as pipe:
        pipe.delete(key)
        pipe.rpush(key, *(json.dumps(m, ensure_ascii=False) for m in messages[-20:]))
        pipe.ltrim(key, -20, -1)
        pipe.expire(key, 86400)
        pipe.set(key + ':head', messages[-1]['id'], ex=86400)
        pipe.execute()


def collection(create=False):
    get_milvus_service()
    if not utility.has_collection(COLLECTION, timeout=10):
        if not create:
            return None
        fields = [FieldSchema(name=name, dtype=DataType.VARCHAR, max_length=64, is_primary=name == 'id')
                  for name in ('id', 'user_id', 'session_id', 'revision')]
        fields.append(FieldSchema(name='vector', dtype=DataType.FLOAT_VECTOR, dim=1024))
        try:
            col = Collection(COLLECTION, CollectionSchema(fields), consistency_level='Strong')
            col.create_index('vector', {'metric_type': 'COSINE', 'index_type': 'IVF_FLAT', 'params': {'nlist': 128}}, timeout=30)
        except Exception:
            if not utility.has_collection(COLLECTION, timeout=10):
                raise
    col = Collection(COLLECTION)
    if create and not col.indexes:
        col.create_index('vector', {'metric_type': 'COSINE', 'index_type': 'IVF_FLAT', 'params': {'nlist': 128}}, timeout=30)
    col.load(timeout=15)
    return col


def index_memory(mid, uid, sid, revision, text):
    vector = generate_embedding(text)
    if not vector:
        raise RuntimeError('摘要向量生成失败')
    col = collection(create=True)
    col.upsert([[str(mid)], [str(uid)], [str(sid)], [revision], [vector]], timeout=15)
    col.flush(timeout=15)


def search_memories(uid, sid, query):
    uid, sid = str(UUID(str(uid))), str(UUID(str(sid)))
    col = collection()
    if col is None:
        return []
    key = 'research:memory-query:v1:' + uid + ':' + hashlib.sha256(query.encode()).hexdigest()
    vector = None
    try:
        value = cache_client().get(key)
        if value:
            vector = json.loads(value)
    except Exception:
        pass
    if not vector:
        vector = generate_embedding(query)
        if not vector:
            raise RuntimeError('记忆查询向量生成失败')
        try:
            cache_client().set(key, json.dumps(vector), ex=300)
        except Exception:
            pass
    hits = col.search([vector], 'vector', {'metric_type': 'COSINE', 'params': {'nprobe': 10}},
                      limit=12, expr=f'user_id == "{uid}" and session_id != "{sid}"',
                      output_fields=['revision'], timeout=15)[0]
    return [{'id': h.id, 'score': float(h.score), 'revision': h.entity.get('revision')} for h in hits]


def delete_index(mid):
    col = collection()
    if col is not None:
        col.delete(f'id == "{UUID(str(mid))}"', timeout=10)
