"""授权先于评分；关键词/向量各取内部 top5，RRF 后可选有界重排。"""
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from app.embeddings import validate_vectors
from app.execution import CallFailure
from app.lore import authorized_chunks, validate_query

TOKENIZER_VERSION = 'nfkc-casefold-cjk-bigram-english-word-v1'
CANDIDATE_K = 5
RRF_CONSTANT = 60


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


def reciprocal_rank_fusion(rankings, constant=RRF_CONSTANT):
    """输入各路 chunk_id 榜单；每路先去重，排名从 1 起，稳定并列。"""
    if type(constant) is not int or constant < 1:
        raise ValueError('INVALID_RRF_CONSTANT')
    scores, ranks = {}, {}
    for route, ranking in enumerate(rankings):
        for rank, chunk_id in enumerate(dict.fromkeys(ranking), 1):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (constant + rank)
            ranks.setdefault(chunk_id, {})[str(route)] = rank
    return [{'chunk_id': i, 'score': scores[i], 'route_ranks': ranks[i]}
            for i in sorted(scores, key=lambda i: (-scores[i], i))]


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


def keyword_scores(query, candidates):
    query_terms = terms(query)
    scored = []
    for chunk in candidates:
        words = terms(chunk.title + '\n' + chunk.text)
        score = len(query_terms & words) / math.sqrt(max(1, len(query_terms) * len(words)))
        if score > 0:
            scored.append((score, chunk))
    return scored


def top_candidates(scored):
    return sorted(scored, key=lambda item: (-item[0], item[1].chunk_id))[:CANDIDATE_K]


@dataclass(frozen=True)
class LoreRetriever:
    snapshot: object
    mode: str = 'keyword'
    cache: object = None
    index: VectorIndex | None = None
    min_score: float = 0.25
    fallback_reason: str | None = None
    reranker: object = None
    fallback_keyword: bool = False

    def __post_init__(self):
        if self.mode not in ('keyword', 'vector_fake', 'vector_real', 'hybrid_fake', 'hybrid_real'):
            raise ValueError('INVALID_RETRIEVAL_MODE')
        if not math.isfinite(self.min_score) or not -1 <= self.min_score <= 1:
            raise ValueError('INVALID_MIN_SCORE')
        if self.reranker is not None and not self.mode.startswith('hybrid_'):
            raise ValueError('RERANK_REQUIRES_HYBRID')
        if self.mode != 'keyword':
            if self.cache is None or self.index is None:
                raise ValueError('RETRIEVAL_UNAVAILABLE')
            if (self.cache.space != self.index.space or self.index.chunks != self.snapshot.chunks
                    or self.mode.split('_')[1] != self.cache.space.mode):
                raise ValueError('EMBEDDING_SPACE_MISMATCH')
            validate_vectors(self.index.vectors, len(self.index.chunks), self.index.space.dimensions)

    async def search(self, query, top_k, *, actor_id, recipient_id, scenario_id, budget, step_id=0, include_diagnostics=False):
        from app.reranking import rerank
        query, top_k = validate_query({'query': query, 'top_k': top_k})
        budget.check()
        candidates = authorized_chunks(self.snapshot, actor_id=actor_id, recipient_id=recipient_id,
                                        scenario_id=scenario_id)
        metadata = {'mode': self.mode, 'fallback_reason': self.fallback_reason,
                    'version': self.snapshot.version, 'tokenizer': TOKENIZER_VERSION}
        keyword = top_candidates(keyword_scores(query, candidates))
        vector, embedding_meta = [], None
        if self.mode != 'keyword':
            metadata.update(space=asdict(self.index.space), min_score=self.min_score)
            if self.cache.space != self.index.space:
                raise ValueError('EMBEDDING_SPACE_MISMATCH')
            if candidates:
                try:
                    result = await self.cache.embed([query], purpose='query', budget=budget, step_id=step_id)
                    embedding_meta = result['metadata']
                    vectors = dict(zip((c.chunk_id for c in self.index.chunks), self.index.vectors))
                    scores = [(cosine(result['vectors'][0], vectors[c.chunk_id]), c) for c in candidates]
                    vector = top_candidates([(s, c) for s, c in scores if s >= self.min_score])
                except (CallFailure, ValueError):
                    if not self.fallback_keyword:
                        raise
                    metadata['fallback_reason'] = 'RETRIEVAL_UNAVAILABLE'
        if self.mode == 'keyword' or metadata['fallback_reason']:
            scored = keyword
        elif self.mode.startswith('vector_'):
            scored = vector
        else:
            scored = None
        routes = {'keyword': [c.chunk_id for _, c in keyword] if not self.mode.startswith('vector_') else [],
                  'vector': [c.chunk_id for _, c in vector]}
        fusion = []
        if scored is None:
            fusion = reciprocal_rank_fusion([routes['keyword'], routes['vector']])
            by_id = {c.chunk_id: c for c in candidates}
            scored = [(row['score'], by_id[row['chunk_id']]) for row in fusion]
        hits = [{**c.reference(), 'title': c.title, 'text': c.text, 'score': score}
                for score, c in scored]
        initial_ids = [h['chunk_id'] for h in hits]
        # 全部候选均来自当前授权池；不将诊断榜单塞进生成 prompt。
        hits, rerank_report = await rerank(query, hits, self.reranker, budget, step_id=step_id)
        budget.check()
        hits = [{**hit, 'rank': rank} for rank, hit in enumerate(hits[:top_k], 1)]
        diagnostics = {'candidate_k': CANDIDATE_K, 'rrf_constant': RRF_CONSTANT,
                       'routes': routes, 'fusion': fusion, 'candidate_ids': initial_ids,
                       'final_ids': [h['chunk_id'] for h in hits], 'embedding': embedding_meta,
                       'rerank': rerank_report}
        budget.trace.emit('retrieval_ranking', 'selected', step_id=step_id,
                          retrieval_details=diagnostics)
        return {**metadata, 'status': 'OK' if hits else 'NO_USABLE_EVIDENCE', 'hits': hits,
                **({'diagnostics': diagnostics} if include_diagnostics else {})}
