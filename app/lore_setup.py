"""显式、独立于首轮的索引构建；当前 CLI 只上传项目附带的虚构种子。"""

from pathlib import Path

from app.embeddings import EmbeddingCache, FakeEmbedding, RealEmbedding
from app.execution import CallFailure, RunBudget, RunLimits, RunStopped
from app.lore import load_lore
from app.retrieval import LoreRetriever, build_index
from app.trace import RunTrace


async def prepare_lore(mode, *, build=False, allow_upload=False, fallback_keyword=False,
                       min_score=None, max_requests=8, report=None):
    if mode == 'off':
        return None, None, 0
    snapshot = load_lore()
    if mode == 'keyword':
        return LoreRetriever(snapshot), None, 0
    if mode not in ('vector_fake', 'vector_real') or not build:
        raise ValueError('向量模式必须显式提供 --build-lore-index，在开局前构建。')
    cache = None
    limits = RunLimits(max_model_requests=min(8, max_requests), max_embedding_requests=8)
    budget = RunBudget(limits, RunTrace('lore-build', 'build', mode='real' if mode == 'vector_real' else 'fake'))
    try:
        if mode == 'vector_real':
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False, encoding='utf-8-sig')
            if min_score is None:
                raise ValueError('真实向量模式需显式设置 --lore-min-score，阈值需自行校准。')
            adapter = RealEmbedding.from_env(allow_upload=allow_upload)
        else:
            adapter = FakeEmbedding()
        cache = EmbeddingCache(adapter)
        index = await build_index(snapshot, cache, budget)
        retriever = LoreRetriever(snapshot, mode, cache, index, min_score=.25 if min_score is None else min_score)
        return retriever, cache, budget.model_requests
    except (ValueError, CallFailure):
        if cache is not None:
            await cache.aclose()
        if not fallback_keyword:
            raise
        return LoreRetriever(snapshot, fallback_reason='RETRIEVAL_UNAVAILABLE'), None, budget.model_requests
    except TimeoutError:
        if cache is not None:
            await cache.aclose()
        raise RunStopped('TURN_TIMEOUT') from None
    except BaseException:
        if cache is not None:
            await cache.aclose()
        raise
    finally:
        if report is not None:
            report['external_requests'] = budget.model_requests
            report['embedding_requests'] = budget.embedding_requests
