"""关键词和向量是可切换的两条路径；授权后才评分、排序、top_k。"""

import math
import re
import unicodedata
from dataclasses import asdict, dataclass

from app.embeddings import validate_vectors
from app.lore import authorized_chunks, validate_query

TOKENIZER_VERSION = 'nfkc-casefold-cjk-bigram-english-word-v1'


def terms(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    result = set(re.findall(r'[a-z0-9_]+', text))
    for phrase in re.findall(r'[\u3400-\u9fff]+', text):
        result.update(phrase[i:i + 2] for i in range(max(1, len(phrase) - 1)))
    return result


def cosine(left, right):
    left, right = validate_vectors([left, right], 2, len(left))
    a, b = math.hypot(*left), math.hypot(*right)
    return max(-1.0, min(1.0, math.fsum((x / a) * (y / b) for x, y in zip(left, right))))


@dataclass(frozen=True)
class VectorIndex:
    space: object
    chunks: tuple
    vectors: tuple


async def build_index(snapshot, cache, budget):
    if len(snapshot.chunks) > 24:
        raise ValueError('LORE_CHUNK_LIMIT')
    result = await cache.embed([c.title + '\n' + c.text for c in snapshot.chunks],
                               purpose='document', budget=budget)
    vectors = validate_vectors(result['vectors'], len(snapshot.chunks), cache.space.dimensions)
    return VectorIndex(cache.space, snapshot.chunks, vectors)


@dataclass(frozen=True)
class LoreRetriever:
    snapshot: object
    mode: str = 'keyword'
    cache: object = None
    index: VectorIndex | None = None
    min_score: float = 0.25
    fallback_reason: str | None = None

    def __post_init__(self):
        if self.mode not in ('keyword', 'vector_fake', 'vector_real'):
            raise ValueError('INVALID_RETRIEVAL_MODE')
        if not math.isfinite(self.min_score) or not -1 <= self.min_score <= 1:
            raise ValueError('INVALID_MIN_SCORE')
        if self.mode != 'keyword':
            if self.cache is None or self.index is None:
                raise ValueError('RETRIEVAL_UNAVAILABLE')
            if (self.cache.space != self.index.space or self.index.chunks != self.snapshot.chunks
                    or self.mode != 'vector_' + self.cache.space.mode):
                raise ValueError('EMBEDDING_SPACE_MISMATCH')
            validate_vectors(self.index.vectors, len(self.index.chunks), self.index.space.dimensions)

    async def search(self, query, top_k, *, actor_id, recipient_id, scenario_id, budget, step_id=0):
        query, top_k = validate_query({'query': query, 'top_k': top_k})
        budget.check()
        candidates = authorized_chunks(self.snapshot, actor_id=actor_id, recipient_id=recipient_id,
                                        scenario_id=scenario_id)
        metadata = {'mode': self.mode, 'fallback_reason': self.fallback_reason,
                    'version': self.snapshot.version, 'tokenizer': TOKENIZER_VERSION}
        scored = []
        if self.mode == 'keyword':
            query_terms = terms(query)
            for chunk in candidates:
                words = terms(chunk.title + '\n' + chunk.text)
                score = len(query_terms & words) / math.sqrt(max(1, len(query_terms) * len(words)))
                if score > 0:
                    scored.append((score, chunk))
        else:
            metadata.update(space=asdict(self.index.space), min_score=self.min_score)
            if self.cache.space != self.index.space:
                raise ValueError('EMBEDDING_SPACE_MISMATCH')
            if candidates:
                result = await self.cache.embed([query], purpose='query', budget=budget, step_id=step_id)
                vector = result['vectors'][0]
                vectors = dict(zip((c.chunk_id for c in self.index.chunks), self.index.vectors))
                for chunk in candidates:
                    score = cosine(vector, vectors[chunk.chunk_id])
                    if score >= self.min_score:
                        scored.append((score, chunk))
        budget.check()
        selected = sorted(scored, key=lambda item: (-item[0], item[1].chunk_id))[:top_k]
        hits = [{**chunk.reference(), 'title': chunk.title, 'text': chunk.text,
                 'score': score, 'rank': rank} for rank, (score, chunk) in enumerate(selected, 1)]
        return {**metadata, 'status': 'OK' if hits else 'NO_USABLE_EVIDENCE', 'hits': hits}
