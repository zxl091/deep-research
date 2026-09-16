"""
Embedding 服务 - 使用阿里 DashScope

功能：
1. generate_embedding - 使用 text-embedding-v4 生成向量
2. rerank_similarity - 使用 DashScope Rerank 重排序
"""

import os
from typing import List, Optional, Tuple
import numpy as np
import httpx
from openai import OpenAI
from llama_index.core.data_structs import Node
from llama_index.core.schema import NodeWithScore
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank

from dotenv import load_dotenv
load_dotenv()


def generate_embedding(
    text: str | List[str],
    api_key: str = None,
    base_url: str = None,
    model_name: str = "text-embedding-v4",
    dimensions: int = 1024,
    encoding_format: str = "float",
    max_batch_size: int = 10
) -> Optional[List[float] | List[List[float]]]:
    """
    生成文本的向量嵌入（使用阿里 text-embedding-v4）

    Args:
        text: 单个文本或文本列表
        api_key: API密钥（默认从环境变量获取）
        base_url: API基础URL（默认从环境变量获取）
        model_name: 模型名称
        dimensions: 向量维度（默认1024）
        encoding_format: 编码格式
        max_batch_size: 最大批量大小（阿里云限制为10）

    Returns:
        单个文本时返回向量，文本列表时返回向量列表
    """
    api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
    base_url = base_url or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    if not api_key:
        print("错误: 缺少 DASHSCOPE_API_KEY 环境变量")
        return None

    trust_env = os.getenv("EMBEDDING_TRUST_ENV", "true").lower() not in {"false", "0", "no"}
    inputs = [text] if isinstance(text, str) else text
    if not isinstance(inputs, list) or not inputs:
        return None
    try:
        with OpenAI(api_key=api_key, base_url=base_url,
                    http_client=httpx.Client(trust_env=trust_env, timeout=30), max_retries=2) as client:
            vectors = []
            for start in range(0, len(inputs), max_batch_size):
                batch = inputs[start:start + max_batch_size]
                response = client.embeddings.create(model=model_name, input=batch,
                    dimensions=dimensions, encoding_format=encoding_format)
                ordered = sorted(response.data, key=lambda item: item.index)
                if len(ordered) != len(batch) or any(len(item.embedding) != dimensions for item in ordered):
                    raise ValueError("向量数量或维度与请求不匹配")
                vectors.extend(item.embedding for item in ordered)
            return vectors[0] if isinstance(text, str) else vectors
    except Exception as exc:
        print(f"Embedding 请求失败: {type(exc).__name__}: {exc}")
        return None


def rerank_similarity(
    query: str,
    texts: List[str],
    top_n: int = None
) -> Tuple[np.ndarray, None]:
    """
    使用 DashScope Rerank 对文本进行重排序

    Args:
        query: 查询文本
        texts: 待排序的文本列表
        top_n: 返回前N个结果（默认返回全部）

    Returns:
        (scores, None) - 分数数组和占位符
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")

    if not api_key:
        print("错误: 缺少 DASHSCOPE_API_KEY 环境变量")
        return np.array([]), None

    top_n = top_n or len(texts)

    # 创建节点列表
    nodes = [NodeWithScore(node=Node(text=text), score=1.0) for text in texts]

    # 初始化 DashScopeRerank
    dashscope_rerank = DashScopeRerank(top_n=top_n, api_key=api_key)

    # 执行重排序
    results = dashscope_rerank.postprocess_nodes(nodes, query_str=query)

    # 提取分数
    scores = np.array([res.score for res in results])

    return scores, None
