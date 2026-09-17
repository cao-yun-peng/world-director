"""显式、独立于首轮的索引构建；当前 CLI 只上传项目附带的虚构种子。"""

from pathlib import Path
import math

from app.embeddings import EmbeddingCache, FakeEmbedding, RealEmbedding
from app.execution import CallFailure, RunBudget, RunLimits, RunStopped
from app.lore import load_lore
from app.retrieval import LoreRetriever, build_index
from app.trace import RunTrace


async def prepare_lore(mode, *, build=False, allow_upload=False, fallback_keyword=False,
                       min_score=None, max_requests=8, report=None, rerank_mode='off', preflight=False):
    if min_score is not None and (not math.isfinite(min_score) or not -1 <= min_score <= 1):
        raise ValueError('INVALID_MIN_SCORE')
    if rerank_mode not in ('off', 'fake', 'real'):
        raise ValueError('INVALID_RERANK_MODE')
    if rerank_mode != 'off' and not mode.startswith('hybrid_'):
        raise ValueError('RERANK_REQUIRES_HYBRID')
    if mode == 'off':
        return None, None, 0
    snapshot = load_lore()
    if mode == 'keyword':
        return LoreRetriever(snapshot), None, 0
    if mode not in ('vector_fake', 'vector_real', 'hybrid_fake', 'hybrid_real') or not build:
        raise ValueError('向量模式必须显式提供 --build-lore-index，在开局前构建。')
    cache = reranker = None
    limits = RunLimits(max_model_requests=min(8, max_requests), max_embedding_requests=8)
    budget = RunBudget(limits, RunTrace('lore-build', 'build', mode='real' if mode.endswith('_real') else 'fake'))
    try:
        if mode.endswith('_real'):
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False, encoding='utf-8-sig')
            if min_score is None:
                raise ValueError('真实向量模式需显式设置 --lore-min-score，阈值需自行校准。')
            adapter = RealEmbedding.from_env(allow_upload=allow_upload)
        else:
            adapter = FakeEmbedding()
        cache = EmbeddingCache(adapter)
        from app.reranking import FakeReranker, RealReranker
        if rerank_mode == 'real':
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False, encoding='utf-8-sig')
        reranker = (FakeReranker() if rerank_mode == 'fake' else
                    RealReranker.from_env() if rerank_mode == 'real' else None)
        if preflight:
            # 只构造配置身份供存档校验，不编码文本；占位索引不得交付运行时。
            from app.retrieval import VectorIndex
            index = VectorIndex(cache.space, snapshot.chunks,
                tuple((1.0,) + (0.0,) * (cache.space.dimensions - 1) for _ in snapshot.chunks))
        else:
            index = await build_index(snapshot, cache, budget)
        retriever = LoreRetriever(snapshot, mode, cache, index,
            min_score=.25 if min_score is None else min_score, reranker=reranker,
            fallback_keyword=fallback_keyword)
        return retriever, cache, budget.model_requests
    except (ValueError, CallFailure):
        if reranker is not None:
            await reranker.aclose()
        if cache is not None:
            await cache.aclose()
        if not fallback_keyword:
            raise
        return LoreRetriever(snapshot, fallback_reason='RETRIEVAL_UNAVAILABLE'), None, budget.model_requests
    except TimeoutError:
        if reranker is not None:
            await reranker.aclose()
        if cache is not None:
            await cache.aclose()
        raise RunStopped('TURN_TIMEOUT') from None
    except BaseException:
        if reranker is not None:
            await reranker.aclose()
        if cache is not None:
            await cache.aclose()
        raise
    finally:
        if report is not None:
            report['external_requests'] = budget.model_requests
            report['embedding_requests'] = budget.embedding_requests
