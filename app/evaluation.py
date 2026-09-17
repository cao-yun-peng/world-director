"""A08 开发集标签验证与按查询宏平均指标；未运行和失败分开。"""
import json
from pathlib import Path
from app.lore import authorized_chunks

DEV_PATH = Path(__file__).resolve().parent.parent / 'data/eval/a08_dev.jsonl'
ANNOTATION_VERSION = 'a08-dev-v1'


def retrieval_metrics(relevant_ids, ranked_ids, k=3):
    if type(k) is not int or k < 1:
        raise ValueError('INVALID_K')
    relevant = set(relevant_ids)
    if not relevant:
        raise ValueError('EMPTY_RELEVANT_SET')
    ranking = list(dict.fromkeys(ranked_ids))[:k]
    return {'recall': len(relevant.intersection(ranking)) / len(relevant),
            'rr': next((1 / rank for rank, i in enumerate(ranking, 1) if i in relevant), 0.0)}


def aggregate(cases, results, k=3):
    if type(k) is not int or k < 1:
        raise ValueError('INVALID_K')
    positive = [c for c in cases if c['retrieval_eligible']]
    scores, missing, failures = [], [], []
    for case in positive:
        row = results.get(case['case_id'])
        if row is None or row.get('status') == 'not_run':
            missing.append(case['case_id'])
            continue
        if row['status'] != 'ok':
            failures.append(case['case_id'])
        scores.append(retrieval_metrics(case['relevant_chunk_ids'],
                      row['ranked_ids'] if row['status'] == 'ok' else [], k))
    incomplete = bool(missing)
    return {'status': 'incomplete' if incomplete else 'complete',
            'positive_queries': len(positive), 'executed_queries': len(scores),
            'not_run': missing, 'service_failures': failures,
            f'Recall@{k}': None if incomplete or not scores else sum(s['recall'] for s in scores) / len(positive),
            f'MRR@{k}': None if incomplete or not scores else sum(s['rr'] for s in scores) / len(positive)}


def load_dev(snapshot, path=DEV_PATH):
    cases = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    required = {'case_id', 'category', 'query', 'fixture_id', 'actor_id', 'recipient_id',
                'scenario_id', 'lore_version', 'retrieval_eligible', 'relevant_chunk_ids',
                'expected_checks', 'forbidden_checks', 'annotation_reason', 'annotation_version',
                'relevant_sources'}
    seen = set()
    for c in cases:
        if (not isinstance(c, dict) or set(c) != required or c['case_id'] in seen
                or c['annotation_version'] != ANNOTATION_VERSION or c['lore_version'] != snapshot.version
                or type(c['retrieval_eligible']) is not bool):
            raise ValueError('INVALID_DEV_ANNOTATION')
        seen.add(c['case_id'])
        allowed = {chunk.chunk_id: chunk for chunk in authorized_chunks(snapshot,
            actor_id=c['actor_id'], recipient_id=c['recipient_id'], scenario_id=c['scenario_id'])}
        gold = c['relevant_chunk_ids']
        if (not isinstance(gold, list) or not all(isinstance(i, str) for i in gold)
                or len(set(gold)) != len(gold) or any(i not in allowed for i in gold)
                or bool(gold) != c['retrieval_eligible']):
            raise ValueError('INVALID_DEV_GOLD')
        # 标签锁定段落/范围，而非将整份文档的任意片段视为相关。
        expected = [chunk.chunk_id for spec in c['relevant_sources'] for chunk in allowed.values()
                    if [chunk.source_id, chunk.paragraph, chunk.start, chunk.end] == spec]
        if set(expected) != set(gold) or len(expected) != len(gold):
            raise ValueError('DEV_CHUNK_CHANGED')
        for field in ('expected_checks', 'forbidden_checks'):
            if not isinstance(c[field], list) or not c[field] or not all(isinstance(x, str) and x for x in c[field]):
                raise ValueError('INVALID_DEV_CHECKS')
    return cases
