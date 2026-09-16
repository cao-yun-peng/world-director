# A04 实现与验收记录

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
