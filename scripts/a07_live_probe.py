"""显式真实验证入口；不默认运行，不把自动输出判成已通过人工质量验收。"""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.execution import RunBudget, RunLimits
from app.lore_setup import prepare_lore
from app.loop_cli import load_model
from app.scene_runtime import SceneStory
from app.trace import RunTrace
from app.world import create_world


async def run_probe(args):
    evidence = {'mode': 'real', 'started_at': datetime.now(timezone.utc).isoformat(),
                'retrieval': [], 'generation': [], 'human_review': 'pending',
                'external_requests': 0, 'max_external_requests': 24}
    output = Path('runs/a07_real_probe.json')
    cache = model = None
    build_report = {}
    try:
        lore, cache, used = await prepare_lore('vector_real', build=True, allow_upload=True,
                                               min_score=args.min_score, max_requests=8, report=build_report)
        evidence.update(space=asdict(cache.space), version=lore.snapshot.version,
                        min_score=lore.min_score, build_requests=used, external_requests=used)
        cases = [
            ('值班室用于什么？', 'lin_yan', 'player'),
            ('来客通常在哪里登记交接事情？', 'lin_yan', 'player'),
            ('交接表的栏目顺序是什么？', 'lin_yan', 'player'),
            ('地下通道密码是什么？', 'lin_yan', 'player'),
            ('沈岚旧目录规则的别名是什么？', 'lin_yan', 'player'),
            ('沈岚旧目录规则的别名是什么？', 'archive_keeper', 'archive_keeper'),
        ]
        for i, (query, actor, recipient) in enumerate(cases):
            limits = RunLimits(max_model_requests=min(2, 24 - evidence['external_requests']))
            budget = RunBudget(limits, RunTrace('a07-real-probe', str(i), mode='real'))
            try:
                result = await lore.search(query, 3, actor_id=actor, recipient_id=recipient,
                                           scenario_id='handover-v1', budget=budget)
                evidence['retrieval'].append({'query': query, 'actor': actor, 'recipient': recipient,
                                               'result': result, 'manual_relevance': 'pending'})
            finally:
                evidence['external_requests'] += budget.model_requests
        if args.generate:
            model = load_model()
            for i in (0, 3, 4):
                query = cases[i][0]
                remaining = 24 - evidence['external_requests']
                story = SceneStory(create_world(f'a07-real-{i}'), max_story_requests=remaining, lore=lore)
                result = await story.turn(query, model, scene_turn_id='T1',
                    limits=RunLimits(max_input_chars=8000, max_model_requests=min(8, remaining)),
                    trace_path=Path('runs/a07_real.jsonl'))
                evidence['external_requests'] += result['model_requests']
                evidence['generation'].append({'query': query, 'result': result,
                    'owners': story.engine.world.owners, 'audit': story.audit_records,
                    'manual_faithfulness': 'pending', 'manual_support': 'pending', 'manual_wording': 'pending'})
        evidence['execution'] = 'finished'
    except Exception as error:
        evidence.update(execution='failed', error_code=getattr(error, 'code', type(error).__name__))
        raise
    finally:
        if 'build_requests' not in evidence:
            evidence['external_requests'] += build_report.get('external_requests', 0)
            evidence['build_report'] = build_report
        if model is not None:
            await model.aclose()
        if cache is not None:
            await cache.aclose()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'evidence': str(output), 'external_requests': evidence['external_requests'], 'human_review': 'pending'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', required=True)
    parser.add_argument('--allow-lore-upload', action='store_true', required=True)
    parser.add_argument('--min-score', type=float, required=True)
    parser.add_argument('--generate', action='store_true', help='另外调用真实聊天模型，运行三条回答路径')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_probe(args)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
