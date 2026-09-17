"""首轮探针 18 次后，仅用余下 6 次预算复测无依据案例。"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.execution import RunLimits
from app.lore_setup import prepare_lore
from app.loop_cli import load_model
from app.scene_runtime import SceneStory
from app.world import create_world


async def run_retest():
    evidence = {'mode': 'real', 'started_at': datetime.now(timezone.utc).isoformat(),
                'max_external_requests': 6, 'external_requests': 0, 'query': '地下通道密码是什么？',
                'previous_probe': 'runs/a07_real_probe.json', 'human_review': 'pending'}
    cache = model = story = None
    report = {}
    try:
        lore, cache, _ = await prepare_lore('vector_real', build=True, allow_upload=True,
                                           min_score=.5, max_requests=6, report=report)
        remaining = 6 - report['external_requests']
        model = load_model()
        story = SceneStory(create_world('a07-real-retest'), max_story_requests=remaining, lore=lore)
        evidence['result'] = await story.turn(evidence['query'], model, scene_turn_id='T1',
            limits=RunLimits(max_model_requests=remaining, max_input_chars=8000),
            trace_path=Path('runs/a07_real_retest.jsonl'))
        evidence['owners'] = story.engine.world.owners
        evidence['audit'] = story.audit_records
        evidence['execution'] = 'finished'
    finally:
        evidence['build_report'] = report
        evidence['external_requests'] = report.get('external_requests', 0) + (story.model_requests if story else 0)
        if model is not None:
            await model.aclose()
        if cache is not None:
            await cache.aclose()
        output = Path('runs/a07_real_io/retest.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'evidence': str(output), 'external_requests': evidence['external_requests'], 'result': evidence['result']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', required=True)
    parser.parse_args()
    print(json.dumps(asyncio.run(run_retest()), ensure_ascii=False, indent=2))
