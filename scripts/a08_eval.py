"""固定开发集四路对照。默认完全离线；Real 必须显式配置并接受独立预算。"""
import argparse
import asyncio
import json
import statistics
from dataclasses import asdict
from pathlib import Path
from time import monotonic
from app.evaluation import aggregate, load_dev, retrieval_metrics
from app.lore import load_lore, CHUNK_VERSION
from app.lore_tools import pack_lore, LORE_TOOL_SCHEMAS, LORE_INSTRUCTIONS
from app.lore_setup import prepare_lore
from app.execution import RunBudget, RunLimits, RunStopped
from app.trace import RunTrace
from app.scene_save import digest
from scripts.a04_demo import call
from scripts.a08_cases import boundary_cases


def delivery_fixture(result,query):
    """真实 pack_lore 函数，固定最小生成输入；不是已运行真实聊天的证据。"""
    data={k:v for k,v in result.items() if k!='diagnostics'}
    messages=[{'role':'system','content':LORE_INSTRUCTIONS},{'role':'user','content':query},
              {'role':'assistant','content':None,'tool_calls':[call('search_lore',call_id='q',
                  arguments=json.dumps({'query':query,'top_k':3},ensure_ascii=False))]},
              {'role':'tool','tool_call_id':'q','content':json.dumps(
                  {'call_id':'q','ok':True,'data':data,'error':None},ensure_ascii=False)}]
    _,delivered=pack_lore(messages,LORE_TOOL_SCHEMAS,8000)
    return list(delivered)


async def evaluate(*,real=False,allow_upload=False,max_requests=120,min_score=.25):
    snapshot=load_lore()
    cases=load_dev(snapshot)  # 无效标签先失败，不能删掉后算漂亮平均。
    report={'dataset':'a08-dev-v1','dataset_sha256':digest(cases),'lore_version':snapshot.version,
            'chunk_version':CHUNK_VERSION,'mode':'real' if real else 'fake',
            'planned_cases':20,'positive_denominator':10,'candidate_k':5,'top_k':3,
            'budget_limit':max_requests,'attempts':[],'used_requests':0,
            'delivery_fixture':'minimal-generation-input-8000-v1; no chat generation',
            'default_decision':'保持旧默认；Fake 不证明真实质量，真实开启需另评延迟与安全。'}
    backend='real' if real else 'fake'
    for name,mode,rerank_mode in [('keyword','keyword','off'),('vector','vector_'+backend,'off'),
                                   ('rrf','hybrid_'+backend,'off'),('rrf_rerank','hybrid_'+backend,backend)]:
        started=monotonic()
        rows,results=[],{}
        cache=lore=None
        build_report={}
        group={'name':name,'mode':mode,'rerank_mode':rerank_mode,'cases':rows,'build':build_report,
               'error':None}
        report['attempts'].append(group)
        try:
            lore,cache,_=await prepare_lore(mode,build=True,allow_upload=allow_upload,
                max_requests=max_requests-report['used_requests'],min_score=min_score,
                rerank_mode=rerank_mode,report=build_report)
            report['used_requests']+=build_report.get('external_requests',0)
            for case in cases[:10]:
                if real and mode!='keyword' and report['used_requests']>=max_requests:
                    rows.append({'case_id':case['case_id'],'status':'not_run','reason':'EVAL_REQUEST_LIMIT'})
                    continue
                b=RunBudget(RunLimits(max_model_requests=max_requests-report['used_requests'],
                    max_attempts=1),RunTrace('a08-eval',case['case_id'],mode=backend))
                begun=monotonic()
                row={'case_id':case['case_id'],'status':'ok','cache_start':'cold_per_backend_query',
                     'ranked_ids':[]}
                try:
                    result=await lore.search(case['query'],3,actor_id=case['actor_id'],
                        recipient_id=case['recipient_id'],scenario_id=case['scenario_id'],budget=b,
                        include_diagnostics=True)
                    row.update(ranked_ids=[h['chunk_id'] for h in result['hits']],
                        candidate_ids=result['diagnostics']['candidate_ids'],
                        delivered_ids=delivery_fixture(result,case['query']),
                        diagnostics=result['diagnostics'],fallback_reason=result['fallback_reason'])
                    row['candidate_metrics']=retrieval_metrics(case['relevant_chunk_ids'],row['candidate_ids'],10)
                    row['final_metrics']=retrieval_metrics(case['relevant_chunk_ids'],row['ranked_ids'])
                    row['delivered_metrics']=retrieval_metrics(case['relevant_chunk_ids'],row['delivered_ids'])
                except (ValueError,RunStopped,TimeoutError) as error:
                    row.update(status='error',reason=getattr(error,'code',type(error).__name__))
                finally:
                    row.update(duration_ms=round((monotonic()-begun)*1000,3),
                               chat_requests=b.chat_requests,embedding_requests=b.embedding_requests,
                               rerank_requests=b.rerank_requests,model_requests=b.model_requests)
                    report['used_requests']+=b.model_requests
                rows.append(row);results[case['case_id']]=row
        except (ValueError,RunStopped,TimeoutError) as error:
            if not rows:
                report['used_requests']+=build_report.get('external_requests',0)
            group['error']=getattr(error,'code',type(error).__name__)
        finally:
            if cache is not None:
                await cache.aclose()
            if lore is not None and lore.reranker is not None:
                await lore.reranker.aclose()
        group['metrics']=aggregate(cases,results)
        group['duration_ms']=round((monotonic()-started)*1000,3)
        durations=[r['duration_ms'] for r in rows if 'duration_ms' in r]
        group['latency']={'raw_ms':durations,'median_ms':statistics.median(durations) if durations else None,
                          'max_ms':max(durations) if durations else None}
    baseline={r['case_id']:r for r in report['attempts'][0]['cases'] if r['status']=='ok'}
    for group in report['attempts']:
        for row in group['cases']:
            if row['status']=='ok' and row['case_id'] in baseline:
                old=baseline[row['case_id']]['final_metrics']
                row['delta_vs_keyword']={key:row['final_metrics'][key]-old[key] for key in old}
    report['boundary_cases']=await boundary_cases()
    report['boundary_failures']=[r['case_id'] for r in report['boundary_cases'] if r['status']!='passed']
    report['unscored_semantic_metrics']=['unsupported_assertions','correct_refusal_or_clarification']
    report['status']='complete' if (not report['boundary_failures'] and
        all(g['metrics']['status']=='complete' for g in report['attempts'])) else 'incomplete'
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--real',action='store_true')
    parser.add_argument('--allow-lore-upload',action='store_true')
    parser.add_argument('--accept-request-budget',action='store_true')
    parser.add_argument('--max-requests',type=int,default=120)
    parser.add_argument('--min-score',type=float)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.max_requests<0:
        parser.error('预算必须非负。')
    if args.real and args.min_score is None:
        parser.error('Real 必须显式设置 --min-score，并预先冻结阈值。')
    if args.min_score is None:
        args.min_score=.25
    if args.real and not (args.allow_lore_upload and args.accept_request_budget):
        parser.error('Real 需 --allow-lore-upload --accept-request-budget；请求上限不是金额上限。')
    output=args.output or Path('docs/a08_retrieval_report_real.json' if args.real else 'docs/a08_retrieval_report.json')
    if args.real and output.exists():
        parser.error('输出已存在；请用 --output 指定新的证据文件。')
    print(json.dumps({'real':args.real,'eval_request_limit':args.max_requests,
        'candidate_k':5,'top_k':3,'min_score':args.min_score,'rerank_timeout_s':5,'rerank_max_tokens':512},
        ensure_ascii=False))
    report=asyncio.run(evaluate(real=args.real,allow_upload=args.allow_lore_upload,
                               max_requests=args.max_requests,min_score=args.min_score))
    output=args.output or Path('docs/a08_retrieval_report_real.json' if args.real else 'docs/a08_retrieval_report.json')
    # 真实证据不能无意覆盖上一次运行。
    if args.real and output.exists():
        parser.error('输出已存在；请用 --output 指定新的证据文件。')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'used_requests':report['used_requests'],
        'metrics':{g['name']:g['metrics'] for g in report['attempts']},'output':str(output)},
        ensure_ascii=False,indent=2))
    return 0 if report['status']=='complete' else 1


if __name__=='__main__':
    raise SystemExit(main())
