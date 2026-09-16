# A04 实现与验收记录

## 最新补充：十轮真实试玩核对（2026-09-16，revision 10）

- 任务 A04-LIVE-REVIEW；L0，唯一课程账本仍为 docs/a04_*。本轮为检查与记录，没有修改业务代码或新发模型请求。
- 来源会话 acf5402a-1d24-44d2-b5ee-362b63abdf90；a04-v6.1 / qwen3.7-plus。10 轮 completed，共 21 次真实请求，无传输重试或格式终止。
- 验证：读取真实 trace 和全部 IO，将保存响应在独立内存世界中重放；请求数、结束状态、版本均一致，退出 0。
- 最终 4 个事件为：台灯发现、移动、信封发现、信封重复发现。信封始终由林砚持有，随其进入储物间。
- 发现：问候仍多查目录；最后一轮误用 discovery-1，经工具错误反馈恢复；跨回合重复观察再次写入知识；管理员离职是无依据背景补写。
- 决定：本条真实端到端执行样本通过，体验获用户肯定；语义与成本仍有改进项，不宣称整体稳定性或阶段总验收通过。revision 9 的一次问候请求目标尚未满足。
- 详情见 [真实试玩核对](a04_live_review.md)，证据为 a04_live_review_evidence.json。后续优先审视知识视图中的 object_id 和跨回合知识去重；本轮不继续堆叠提示词。

## 最新补充：问候触发无关查询（2026-09-16，revision 9）

- 任务 A04-QUERY-SCOPE；L0 本地课程原型，继续沿用 docs/a04_* 账本。
- 真实来源：run_id=6618fa58-9207-4d5c-8dd8-ac94ef6721b4，a04-v6 / qwen3.7-plus。主 trace 第 214—227 行在首次读取时存在，显示 get_visible_scene → 两个 inspect_object → end_turn，3 次请求，提交版本 2。用户输出显示事件 2。
- 证据限制：后续读取时 runs 目录已不存在，原始 IO 未成功读取；目录消失原因未知。不宣称已经核对具体工具参数、返回正文或模型内部动机。
- 可修正的问题：原提示“目录没有提供时先查”未明确查询的必要性，end_turn 的说明也未强调可以直接调用。
- 修正：协议标记 a04-v6.1；只更新查询范围提示与 end_turn 描述。已有信息足够就直接答复，目录足够就结束，不为丰富回复探索无关物品。
- 不变量：工具选择仍由模型完成；没有添加关键词路由或分类调用；原子提交、参数校验、预算和幂等不变。
- 验收目标：问候直接 end_turn，1 次请求、0 个发现事件；必要的目录/细节查询仍可使用。
- 状态：提示修正完成；9 项现有终结工具回归通过（退出 0），记录见 a04_query_scope_tests.txt；真实语义改进未验证，本次真实请求 0 次。此前 161 项是离线编排证据，不能作为模型查询选择正确的证明。当前证据哈希见 a04_query_scope_evidence.json。

## 最新补充：End with tool 重构（2026-09-16，revision 8）

- 任务 A04-END-TOOL；用户授权按终结工具方案实施。沿用 docs/a04_* 课程账本，L0 本地教学原型；不初始化长期治理。
- 任务状态：离线实现、教学与验证完成；真实服务兼容性待试玩。生命周期阶段无独立 revision，不声明生产 Gate 通过。
- 范围：A04 循环、终结工具、CLI、trace、诊断/演示脚本及对应测试和教学。A01—A03、世界裁定和身份归属规则保持原契约。
- 目标：end_turn(reply)、move(destination_id)、give(object_id, recipient_id) 统一使用原生工具调用；删除正文决策 JSON 和专用格式修复。
- 不变量：查询只读；终结调用独占批次；同轮观察与行动一次提交；非法行动全部回滚；turn_id 幂等；共享预算和取消；保留模型输入输出证据。
- 验收：回答、查询后结束、参数修正、混合批次、拒绝、/retry、取消、SDK 请求与 IO 日志一致，以及全部课程回归。
- 真实请求：本任务不消耗此前已经用完的 6 次诊断授权；只运行离线模型和 MockTransport。真实兼容性留待用户试玩。
- END-T1：A04 专项 61 项通过，全部课程 161 项通过（退出 0），完整记录 a04_end_tool_tests.txt。
- END-D1：九次离线互动共 14 次模拟请求，最终 revision=6 / events=6；重复 turn 请求数 0。
- END-F1：六类故障演示完成（退出 0），未提交及失败查询不改变世界；失败查询后的澄清回复可为 completed。
- 代码与教学见 [End with tool](a04_end_with_tool.md)，演示产物位于 a04_end_tool/，证据哈希见 a04_end_tool_evidence.json。
- 本次删除 A04 正文决策 Schema、解析和专用格式修复，保留工具参数校验；移动/给物后的额外叙述仍在原预算内执行。

## 最新补充：Schema 真实试玩仍有格式失败（2026-09-16，revision 7）

核对用户的 a04-v5 / qwen3.7-plus 运行：房间目录与信封观察分别在 2、3 次请求内完成，没有格式纠正；后续“怎样打破限制”却在 strict JSON Schema 下连续返回两次纯文本。
因此 revision 6 的离线测试仅证明本地接线与边界处理，不能证明真实服务稳定遵守 Schema。
另一回合的 OBJECT_UNAVAILABLE 来自模型把 discovery-0 知识编号当成物品 ID；正确物品是 envelope_01。玩家身份也尚未映射，不能将玩家自动当作 other_npc。
本次已离线复现错误引用及两次解析失败，验证错误引用不改变世界；没有新增模型请求或修改业务代码。
详情与证据见 [Schema 真实试玩补充](a04_schema.md) 和 a04_schema_evidence.json。后续分别处理实体引用/玩家身份与服务端格式违例，尚未完成真实稳定性验收。

## 最新补充：JSON Schema 决策约束（2026-09-16，revision 6）

用户要求先采用 JSON Schema。运行协议升级为 a04-v5，A04 决策请求改为 json_schema + strict=true，禁止额外字段；默认模型改为 qwen3.7-plus。
Schema 集中限制对象形状、kind 枚举与字段类型；按 kind 变化的必填组合仍由已有 parse_decision / parse_action 校验。
保留原生工具循环、关闭思考、一次格式纠正、输入输出 trace 及世界裁定。未添加供应商分支、依赖或自动降级。
新增一项真实 SDK + MockTransport 工具回合测试，全量 162 项离线测试通过；没有新增真实模型请求，服务端联合约束效果待验证。
旧超时用例在 json_object 对照下同样失败；仅将测试预算同比放大，断言和业务预算未变，详见 [Schema 说明](a04_schema.md)。
测试输出：a04_schema_tests.txt；交付证据：a04_schema_evidence.json。下面 revision 5 及更早记录保留为历史证据。

## 最新补充：模型输入输出持久化（2026-09-16，revision 5）

用户明确授权保存模型调用的实际输入和原始输出。A04 决策/叙述每次 attempt 均保存 input、可获得的 output 与 end，主 trace 用 io_ref 关联。
运行协议升级为 a04-v4，trace_version=3。API 鉴权配置不写日志；正文目录在 runs 下且加入 Git 忽略。
新增 8 项离线测试；全部 161 项通过。SDK + MockTransport 验证日志输入与实际 HTTP 请求体一致，并验证坏 JSON 原文、空 choices、工具上下文、重试、取消、超时、落盘失败和幂等重放。
输出：a04_model_io_tests.txt；当前源码和证据哈希：a04_model_io_evidence.json；用法见 [输入输出排查](a04_model_io.md)。
本次没有新增真实模型调用；另生成 runs/a04_io_example.jsonl 的 fake 示例供本地查看。
下面 revision 4 的真实对照仍是历史证据，不能宣称模型格式问题已经修复。

## 最新补充：重复故障与 trace 诊断（2026-09-16，revision 4）

用户再次报告格式失败，并要求补充 trace。已核对原 run 的两次 JSON 解析失败。
新增运行版本/预算、请求与响应结构、JSON 错误位置、决策成功及格式纠正记录；CLI 显示日志路径。
全量 153 项测试通过，当前输出为 a04_trace_diagnostics_tests.txt。
实际完成 4 次有限真实对照：两次合法 clarify，两次字符串未闭合；两种请求参数组合各失败一次。
这证明格式问题仍可复现，不宣称真实服务已修复；没有执行对照中的工具或修改世界。
详见 [trace 排查记录](a04_trace_diagnostics.md)。以下 revision 3 / 2 为历史记录。

日期：2026-09-15。任务：A04 循环、异步、重试与运行轨迹；状态：代码与离线教学交付完成；阶段 A 总验收仍待真实试玩与独立练习。

## 2026-09-16 后续修复（revision 3）

用户的真实问答出现 INVALID_DECISION；已核对对应日志，确认模型请求返回后未通过本地决策协议，未提交世界。
新增 JSON 输出约束、预算内一次格式纠正、脱敏字段诊断与针对性的 CLI 提示。
新增 9 项专项及全部 146 项测试通过；详见 [决策格式修复](a04_decision_fix.md)。
本次没有重新调用真实模型；下面 revision 2 的 137 项结果及 a04_evidence.json 是 2026-09-15 的历史快照。
最新修复证据为 a04_decision_fix_evidence.json；阶段 A 总验收仍待完成。

## 开工契约（revision 1）

- 唯一课程证据沿用 `docs/a04_*`，不初始化长期项目治理；Project-to-Act 只读发现为 unconfigured、无外部账本。
- 风险：L0 本地教学原型。工作处于功能开发与测试；不宣称生产阶段通过。
- 本地 A03 已实现；开工基线为 Python 3.12.10、100 项 unittest 通过。
- 保留开工时 `app/actions.py`、`app/main.py`、`app/world_runtime.py`、`tests/test_a03.py` 及 A03 协议修复文档中的未提交修改。
- 范围：复用 A03 裁定，新增异步循环、共享预算、查询执行器、原生异步模型接口、JSONL、CLI、测试和教学。
- 不变量：可信身份、原生调用配对、只读快照、完整提交、提交前取消无副作用、提交后不重复结算。
- 验证：A04 专项、全部 unittest、离线连续演示；真实模型演示单独标记，不能用 Fake 替代。
- 学习者独立变式与解释保留为待练习；AI 编写的代码不能算独立能力通过。

## 验收结果（revision 2）

任务 A04 的离线工程检查通过。生命周期治理仍未初始化，不填写虚构的阶段 revision 或生产 Gate。
当前 Git 基线：`f2d653b774946147f19e5abdd29363fd1fc05765`；本次成果保留在工作区，没有自动提交用户已有的未提交修改。

| 证据 ID | 实际执行 | 结果 | 产物 |
|---|---|---|---|
| A04-T1 | unittest discover，test_a04*.py | 37 项通过，退出 0 | a04_tests.txt |
| A04-T2 | unittest discover，全部 tests | 137 项通过，退出 0 | a04_all_tests.txt |
| A04-D1 | scripts.a04_demo | 9 次 Fake 互动，17 次模拟请求，最终 revision=6、事件=6 | a04_demo.json、a04_demo_output.txt |
| A04-F1 | scripts.a04_faults | 六类场景通过，退出 0 | a04_scenarios.json、a04_fault_trace.jsonl |
| A04-C1 | exercises.a04_parallel | 五任务，峰值 2，结束 active=0 | a04_parallel_output.txt |
| A04-S1 | 固定 smolagents 源码及官方文档阅读 | 静态对照完成，没有运行框架 | a04_source_notes.md |

环境：Windows / PowerShell，Python 3.12.10，项目虚拟环境；依赖未变更。
所有测试和离线脚本不读 .env、不发送真实 API 请求。输出只规范化换行，不改测试结果。
证据对应本工作区内容；源码和证据 SHA-256 见 `a04_evidence.json`。后续修改影响文件后应重新验证。

### 六类场景

| 场景 | 实际观察 |
|---|---|
| 正常 | 细节查询 → finish → 叙述，3 次请求，新增 1 条发现 |
| 坏参数 | 工具返回 INVALID_ARGUMENTS；不执行等待接口、不重试坏调用，世界不变 |
| 越权 | OBJECT_UNAVAILABLE，不在日志回显被拒对象 ID，世界和个人知识不变 |
| 超时 | TURN_TIMEOUT，1 次模拟请求，版本和事件都保持 0 |
| 循环 | STEP_LIMIT，恰好 4 次决策，没有额外总结，没有提交发现 |
| 重复提交 | 首次行动 1 条事件；重发请求为 0 次模型调用；同 ID 改请求产生冲突 |

坏参数场景中的 2 次模型请求分别是发起查询和收到错误后终止；不是同一个坏工具自动重试两次。
专项测试另覆盖无 ID 整批拒绝、两个同名工具乱序完成、临时失败重试、叙述超时回退、精确 deadline、提交前后取消、锁排队耗时、日志写失败和 CLI 关闭。

### 关键设计与修改

- `app/async_runtime.py` 单独保存 A04 异步入口，旧同步课程入口保留。复用既有 ActionProposal、原生工具 Schema 和 WorldEngine。
- `app/execution.py` 统一步数之外的时间、请求和尝试预算；SDK 内部重试关闭。
- `app/query_executor.py` 共用 Semaphore，先复用原白名单/参数/身份检查，再进入可等待查询。
- `app/world.py` 增加递归只读 WorldSnapshot；同轮查询不读取可变账本。
- `app/engine.py` 把多项候选形成完整接纳，任何裁定失败均回到原世界，最后一次接纳前检查 deadline。
- `app/model.py` 增加原生异步适配器；原有同步适配器接口不变。
- `app/trace.py` 记录允许的元数据；模型提供的 call_id 以摘要入日志，协议中仍保留原值。
- `app/main.py` 增加 loop 模式，交给 `app/loop_cli.py` 管理异步生命周期和取消。

本日串行处理同一个世界的回合。turn_lock 防止 A04 请求重叠；不承诺混用旧同步入口或跨事件循环/多进程调用的并发安全。
真实模型的语言仍可能偏离事实；确定性回执始终单独保留，世界只由裁定更新。

### 一次失败的定位

打开 `a04_scenarios.json`，找到 case=loop 的 run_id，再筛选 `a04_fault_trace.jsonl`。
该 run 有四次成功查询尝试，最后却是 STEP_LIMIT，committed_revision=null，且没有 commit 记录。
这说明查询确实返回过数据，但观察仍是候选；场景记录证明 revision 0→0、事件 0→0。
不能仅凭 tool 的 status=ok 宣称知识已经进入世界。

### 发现与修正记录

首轮专项执行为 33 项，1 个失败、6 个错误：六个错误来自测试在同步 setUp 中创建需要运行中事件循环的预算；一个失败来自测试自行把敏感被测参数拼进了可信 turn_id。
分别改为 asyncSetUp 和独立回合编号后通过。没有通过删掉边界断言来掩盖失败。
后续补齐四项 CLI/显式真实入口测试，最终 37 项专项、137 项全量通过。

## 未执行项与交接

- **真实 8—10 轮试玩未执行**：已提供 `scripts.a04_smoke --real --max-model-requests 48`，本轮没有读取或使用真实密钥。真实自由叙述、真实依赖查询与服务兼容性尚未验收。
- **独立变式未通过**：已交付 `exercises/a04_parallel.py` 观测台；学习者需自己将 PARALLEL 从 2 改为 1、运行并解释结果。
- **A04 commit 未创建**：工作区包含用户已有 A03 修改；本轮交付可审阅的工作区成果，未把这些修改代为打包提交。
- 无连续计时，不填写伪精确工时。AI 帮助范围：实现、测试、参考讲解和示例证据；学习者的预测、复写、解释与真实试玩仍需自行完成。
- 世界仅在内存中，无持久事务/世界恢复；异步取消是协作式的，不保证强停远端计算；JSONL 不是完整回放数据。

下一步：从 `a04_practice.md` 第 2 节写出提交前/后的预测，运行离线示例，再读 decide 与 commit_turn。阶段 A 不在本轮自动评分或宣称通过。
