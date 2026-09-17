# A08 连续试玩证据

**本轮是确定性 Fake 工程演示，真实模型自由文字试玩仍待完成。**

[逐轮 JSON](a08_playthrough.json) 保留 20 个不同 scene_turn_id、角色、前后 revision、
事件 ID、归属、可见记忆 ID、实际交付 lore 引用、调用分项及停止状态。
路线位于 scripts/a08_demo.py 的 ROUTE；Fake 响应只用于锁定工程检查点。

主局第 1—10 轮在一个进程执行，第 10 轮保存后进程结束；第二个进程加载，比较完整快照，
重放第 10 轮得到零新增聊天/Embedding/重排及零事件，然后执行 11—20。
三个角色均有互动；第 20 轮先由林砚合法 give，再由周澈响应已提交事件，结局 handed_over。
前 19 轮信封一直归林砚，没有提前 TransferEvent。第 13 轮私密原句未进入周澈请求。

主局 20 轮共 34 次 Fake 聊天尝试，关键词路径的 Embedding/重排均为 0。
独立新局再用 1 次 Fake 聊天得到 deferred，信封仍在林砚手上。演示批次合计 35/180，
其中付费请求 0。相反分支不计入主局的 20 轮。

复跑：`python -X utf8 -m scripts.a08_demo`。这会默认覆盖当前 a08_playthrough.json；
需要保留每次证据时用 `--output 新路径`，或使用 a08_verify 的新时间戳目录。

真实试玩入口（需本人显式接受供应商费用；本轮未执行）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine scene --scene-profile a08 --enable-real --lore-mode keyword
```

A08 profile 显式使用每故事 20 轮/80 次、批次 180 次；更低总上限可加
`--max-model-requests 40`。/new 不补充批次额度。80 次不是保证玩满 20 轮，也不是金额上限。
仍保留每轮 8 次、30 秒、8000 Unicode 字符及现有聊天 512 输出 token 上限。

真实检索评测是独立批次，已有真实向量配置不等于重排已配置。
Real 重排读取 RERANK_API_KEY、RERANK_MODEL、RERANK_BASE_URL，显式使用 JSON 排序协议、512 输出 token、
SDK 重试为 0。先确认供应商支持该协议与可接受费用，再运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.a08_eval --real --allow-lore-upload --accept-request-budget --max-requests 120 --min-score 0.5 --output docs/a08_real_trial_01.json
```

0.5 仅引用现有 A07 小样本阈值供明确选择，不代表已为 A08 校准。
命令需全部 Real 配置齐全；本轮没有运行。生成前会显示配置、额度；耗尽即标未运行，不偷偷扩额。
是否默认启用仍需 MRR@3 改善、Recall@3 不下降、安全无退化并满足预先约定延迟/请求上限。
