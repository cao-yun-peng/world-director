# A06 实施契约

2026-09-17，任务 A06-IMPLEMENT-20260917，revision 2，active。风险 L0，本地虚构教学。执行者 Codex；不委派子代理。

唯一进度与验收入口为 docs/a06_results.md，沿用用户给定课程计划。Project-to-Act 本次 --check 为 unconfigured；未初始化平行账本，正式生命周期 revision 不适用。当前工作为功能开发及离线验证，不作生产或整课学习通过结论。

基线：HEAD 54cf484 加已完成的 D036 wait 工作区成果，199 项测试通过。保留这些未提交文件及原始预测。本任务不读取 .env、不自动调用真实模型、不提交 Git。

允许路径：app/{director,scene_runtime,scene_cli,scene_tools}.py 新模块；app/{engine,execution,async_runtime,memory_runtime,memory,main,trace,world,world_runtime}.py 的必要兼容扩展；tests/test_a06*.py；scripts/a06*.py；exercises/a06_replan.py；docs/a06*；README.md。

接口与不变量：

- 场景启用、玩家选择、导演机会、结束事件只由 WorldEngine 接纳；其状态从已提交事件及当前世界确定性投影。不给模型通用状态补丁入口。
- 固定 handover-v1 开局配置，主线为信封交接；公开进度仅向 player、lin_yan、other_npc 授权。沈岚没有该线程的元数据，自己的私语仍独立。
- 导演只看允许的位置元数据、交接进度、受众与公开来源，不能读取全体历史或私语正文。计划是可丢弃缓存，最多两步，同场景轮最多重规划一次。
- 事件候选严格字段、模板、来源、版本、前提、受众校验；稳定机会业务键去重，每场景轮最多一个机会。
- 默认最多一个响应者，显式最多两个；注册、位置、输入授权先于相关性排序。私语只交给指定接收者，其他角色只能收到公开选择或自己的合法事件刺激。
- 场景工具的理由与 source_refs 是待核验解释；引用只允许当前角色有权看见的已提交来源。旧 memory/loop 工具兼容，不把新增理由字段强塞进旧 schema。
- 整场景共享 8 次模型请求和 30 秒 deadline（包含排队、重试、叙述、可选结尾），每次 messages + tools 不超过 8000 Unicode 字符；子角色保留原步数限制。
- 场景 ID 与参数摘要先去重，再选择/规划/调用。中途失败保存已提交子步骤并返回 partial/stopped；相同请求重发只读保存结果，改参数冲突。取消后释放锁。
- handed_over 要有效推进选择、真实林砚到周澈 TransferEvent 和当前归属；deferred 要有效暂缓选择、信封仍归林砚且线程未解决。结束只接纳一次，润色失败回退规则结果，新普通行动零模型请求、零世界变更。

验证：先离线两分支与改选重规划，再用 test_a06*.py 覆盖权限、严格候选、顺序新视图、重复、预算、取消、部分完成与结束后行为；最后全量回归及 A05 原演示（另存输出，不覆盖历史）。真实试玩需显式入口及总上限，当前只实现入口与离线模拟验证。

交付证据保存实际命令、退出码、工件哈希；AI 参考源码笔记与独立练习分开，保留未完成学习项。下一检查点为最小两分支可运行，然后补反例与完整教学记录。

## 完成检查点（revision 3）

任务状态：离线工程实现完成，已 review 并完成 36 项专项、228 项全量、两分支、改选、同地/异地变式与 A05 回归；证据与交接均在 a06_results.md。新增 world / world_runtime 的选择约束及结束后提前拒绝属于原 A06 范围内的必要边界补齐。

发现的 deadline 分类问题已修复，原失败输出保留。当前上下文与工件绑定见 a06_evidence_manifest.json。Project-to-Act 仍为 unconfigured，不伪造正式阶段转换；真实模型和学习者成绩继续待验证。
