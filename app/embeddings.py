"""可注入异步编码器、严格向量校验和进程内计算缓存；不缓存权限决定。"""

import asyncio
import hashlib
import math
import os
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from app.execution import TransientFailure
from app.lore import text_hash


@dataclass(frozen=True)
class EmbeddingSpace:
    mode: str
    provider: str
    model: str
    version: str
    dimensions: int
    preprocessing: str = 'nfkc-bigram-v1'
    query_prefix: str = ''
    document_prefix: str = ''

    def __post_init__(self):
        if self.mode not in ('fake', 'real') or type(self.dimensions) is not int or self.dimensions < 1:
            raise ValueError('INVALID_EMBEDDING_SPACE')
        if not all(isinstance(s, str) and s for s in (self.provider, self.model, self.version, self.preprocessing)):
            raise ValueError('INVALID_EMBEDDING_SPACE')


def validate_vectors(vectors, count, dimensions):
    if not isinstance(vectors, (list, tuple)) or len(vectors) != count:
        raise ValueError('EMBEDDING_COUNT')
    result = []
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or len(vector) != dimensions:
            raise ValueError('EMBEDDING_DIMENSION')
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in vector):
            raise ValueError('EMBEDDING_NONFINITE')
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('EMBEDDING_ZERO_OR_OVERFLOW')
        result.append(tuple(float(n) for n in vector))
    return tuple(result)


class FakeEmbedding:
    """哈希词面向量，只证明数学/排序；不能证明真实中文语义。"""
    def __init__(self, *, dimensions=256, model='fake-bigram-v1'):
        self.space = EmbeddingSpace('fake', 'local', model, '1', dimensions)
        self.calls = 0
        self.closed = False

    async def embed(self, texts, purpose):
        from app.retrieval import terms
        self.calls += 1
        vectors = []
        for text in texts:
            vector = [0.0] * self.space.dimensions
            for term in terms(text) or {text}:
                index = int.from_bytes(hashlib.sha256(term.encode('utf-8')).digest()[:8], 'big')
                vector[index % self.space.dimensions] += 1.0
            vectors.append(vector)
        return vectors

    async def aclose(self):
        self.closed = True


class RealEmbedding:
    """OpenAI 兼容 embeddings 协议；独立配置，不推断聊天模型支持向量。"""
    def __init__(self, api_key, model, base_url, dimensions, *, allow_upload=False, client=None):
        if not allow_upload:
            raise ValueError('LORE_UPLOAD_NOT_ENABLED')
        parsed = urlsplit(base_url)
        if (not api_key or not model or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('EMBEDDING_NOT_CONFIGURED')
        from openai import AsyncOpenAI
        self.space = EmbeddingSpace('real', base_url.rstrip('/'), model,
                                    'provider-unpinned', dimensions, 'raw-text-v1')
        self.client = client or AsyncOpenAI(api_key=api_key, base_url=base_url,
                                           max_retries=0, timeout=10)

    @classmethod
    def from_env(cls, *, allow_upload=False):
        names = ('EMBEDDING_API_KEY', 'EMBEDDING_MODEL', 'EMBEDDING_BASE_URL', 'EMBEDDING_DIMENSIONS')
        values = [os.getenv(n, '').strip() for n in names]
        if not all(values):
            raise ValueError('EMBEDDING_NOT_CONFIGURED')
        try:
            dimensions = int(values[3])
        except ValueError:
            raise ValueError('EMBEDDING_NOT_CONFIGURED') from None
        return cls(*values[:3], dimensions, allow_upload=allow_upload)

    async def embed(self, texts, purpose):
        from openai import APIConnectionError, APITimeoutError, APIStatusError
        try:
            result = await self.client.embeddings.create(model=self.space.model, input=list(texts),
                dimensions=self.space.dimensions, encoding_format='float')
        except (APIConnectionError, APITimeoutError):
            raise TransientFailure() from None
        except APIStatusError as error:
            if error.status_code == 429 or error.status_code >= 500:
                raise TransientFailure() from None
            raise ValueError('EMBEDDING_PROVIDER_ERROR') from None
        if (len(result.data) != len(texts) or
                sorted(item.index for item in result.data) != list(range(len(texts)))):
            raise ValueError('EMBEDDING_COUNT')
        return [item.embedding for item in sorted(result.data, key=lambda item: item.index)]

    async def aclose(self):
        await self.client.close()


class EmbeddingCache:
    def __init__(self, adapter):
        self.adapter = adapter
        self._entries = {}
        # 小库串行构建，等待者受自己 deadline 限制；不遗留脱离预算的后台请求。
        self._lock = asyncio.Lock()

    @property
    def space(self):
        return self.adapter.space

    async def embed(self, texts, *, purpose, budget, step_id=0):
        if purpose not in ('query', 'document') or not all(isinstance(t, str) and t.strip() for t in texts):
            raise ValueError('INVALID_EMBEDDING_INPUT')
        space = self.space
        prefix = space.query_prefix if purpose == 'query' else space.document_prefix
        prepared = [prefix + text for text in texts]
        keys = [(space, purpose, text_hash(text)) for text in prepared]
        async with asyncio.timeout_at(budget.deadline):
            async with self._lock:
                budget.check()
                missing = dict((key, text) for key, text in zip(keys, prepared) if key not in self._entries)
                pending = {}
                entries = list(missing.items())
                for offset in range(0, len(entries), 10):
                    batch = entries[offset:offset + 10]
                    vectors = await budget.call(
                        lambda: self.adapter.embed([text for _, text in batch], purpose),
                        kind='embedding' if space.mode == 'real' else 'local_embedding', step_id=step_id)
                    vectors = validate_vectors(vectors, len(batch), space.dimensions)
                    if self.space != space:
                        raise ValueError('EMBEDDING_SPACE_CHANGED')
                    pending.update(zip([key for key, _ in batch], vectors))
                budget.check()
                self._entries.update(pending)  # 整个调用校验成功后才发布。
                return {'vectors': [list(self._entries[key]) for key in keys],
                        'metadata': {**asdict(space), 'purpose': purpose,
                                     'cache_hits': len(keys) - len(missing)}}

    async def aclose(self):
        await self.adapter.aclose()
