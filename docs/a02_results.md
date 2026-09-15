# A02 实现总结与验收记录

## 结论

A02 的意图理解、两个只读工具、原生 Function Calling 闭环及 A01 兼容接入已完成。
最终 A02 专项 **26 项通过**，全部回归 **58 项通过**；四条真实样例复验通过。
本次真实请求累计 **12 次**，usage 合计 **6,220 tokens**（输入 5,958，输出 262）；未取得账单金额，不推算实际费用。

- 日期：2026-09-15。
- 基线 commit：211e9943f66c61f0e31d247fbde538d8c1ca1d59；交付为当前工作区改动，未创建新 commit。
- 实际模型：qwen-plus；服务地址沿用北京 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- 单次请求：非思考、非流式、max_tokens=512、timeout=30 秒、max_retries=0。
- 原 A01 generate 的 max_tokens=256 与行为保留。
- 项目记录模式：检查结果为 unconfigured、无外部账本。按本项目阶段文档习惯记录本次工作，没有初始化管理框架；无生命周期 revision，不虚构生产门禁。
- AI 帮助范围：读取计划、设计与编写代码/测试、执行验证、修复样例偏差、整理两份交付文档。用户补充附件并明确授权真实演示；独立学习掌握程度未评估。
- 实际耗时：未连续计时，不提供伪精确工时；真实请求时间戳保留在证据中。

## 实现与计划对应

| 内容 | 实际实现 |
|---|---|
| D008 意图识别 | actions.py 解析 talk / inspect / clarify；检查固定字段、类型、枚举与跨字段组合 |
| D009 只读观察 | tools.py 的 get_visible_scene / inspect_object；原样使用附件 objects.json |
| D010 分发 | 显式白名单、稳定的 call_id / ok / data / error 结构 |
| D011 权限 | 可信 actor_id 由 CLI 传入；目录与查询共用地点、公开/私有授权筛选 |
| D012 行动提议 | 提议没有执行或写世界的能力；talk/clarify 直接消费已校验 reply |
| D013 原生闭环 | complete 保留 tool_calls；auto 选择、顺序执行、按 ID 反馈、none 生成最终文本 |
| D014 验收 | 离线矩阵、SDK MockTransport、真实样例及 A01 全部旧测试 |

核心流程：

```text
可信身份与会话校验
  → 第一次请求：JSON 意图提议
      → talk / clarify：采用 reply
      → inspect：第二次请求（tools + auto）
          → 整批检查协议、数量与唯一 ID
          → 参数校验、注入身份、只读查询（最多两个）
          → 原 assistant 请求 + 相同 ID 的 tool 结果
          → 第三次请求（tools + none）：最终角色文本
  → 成功后一次性构造新的 user/assistant 历史
```

不存在、隐藏、异地对象均返回相同 OBJECT_UNAVAILABLE；异常正文不交给模型。全批协议错误在任何函数执行前拒绝。参数或业务错误则可按原 ID 回传，模型解释后提交完整一轮。网络、截断、非法意图、再次要工具和预算不足均有限结束，不提交半轮。

## 与计划的差异、发现与修复

1. **身份参数已提前完成**：工作区原本已有 session.py、storage.py、main.py 和 test_session.py 的未提交身份校验改动。此次直接复用；没有把会话中的 actor_id 当成外部身份证明，也没有覆盖这些既有修改。
2. **教学数据来源已补齐**：开工时仅有任务摘要，随后用户提供完整任务卡及 JSON。本实现使用原始 JSON，未自拟物品描述；objects.json 与附件内容一致。
3. **首次真实“给物”样例发现偏差**：模型返回 kind=clarify，但 reply 是“信封里装着什么？”，没有明确说明动作不支持。旧演示脚本只检查 kind，因而错误地将此样例标为 passed。人工复核认定不符合任务要求；保留原日志，不把它算作最终通过证据。
4. **修复与复验**：强化“边界回复必须明确说明动作当前不支持”的提示，增加给物样例的措辞检查及回归测试。第二轮回复“目前还不能接收信封。”；四条样例全部通过。首次 6 次 + 复验 6 次，累计 12 次后停止真实调试。
5. **离线入口选择**：无 Key 时 tools CLI 明确退出；离线工具链由 tests/test_a02.py 的脚本化 complete 测试，不实现假装自然意图理解的演示模型。A01 默认离线对话仍可用。
6. **轨迹脱敏与重放取舍**：常规轨迹记录合法调用请求和允许输出的结果，删除失败调用的原始参数及未知工具名；不记录玩家原话与最终文本。这是数据最小化选择，因此常规失败轨迹不能完整重放。真实证据脚本只处理固定教学新会话，单独保留实际适配器消息及回复。
7. **额外预算入口**：CLI 提供 --max-model-requests，默认每进程 12 次；仍保持每轮最多 3 次。预算不跨进程持久化，重新启动不代表账户费用清零。

目前没有未完成的 A02 核心工程项。真实验证是有限样本，不宣称任意输入都能稳定正确识别或绝不产生无依据的语言。

## 验收证据

| 证据 | 方法与结果 | 文件 |
|---|---|---|
| A02-BASELINE | 开工前运行 A01 全部测试：32 项通过 | 本次执行记录；最终全量再次覆盖 |
| A02-UNIT | unittest discover -s tests -p test_a02.py -v：26 项，0 失败/错误 | [a02_tests.txt](a02_tests.txt) |
| A02-REGRESSION | unittest discover -s tests -v：58 项，0 失败/错误 | [a02_all_tests.txt](a02_all_tests.txt) |
| A02-REAL-20260915 | qwen-plus 实际请求累计 12 次；最终四条通过，退出码 0 | [a02_real_evidence.json](a02_real_evidence.json) |
| A02-FORMAT | git diff --check：通过 | 本次执行记录 |

最终测试输出通过 unittest 的 discover API 写入文件，等价于表中命令；上述 CLI 命令也在实施过程中实际运行过。SDK 测试使用真实 OpenAI 客户端与 MockTransport，验证序列化后的 tools、tool_choice、enable_thinking、stream、max_tokens 及两次同名调用的回传 ID。

真实证据保存实际请求/响应、usage、最后回复与源文件 SHA-256。证据有效范围是该次验证对应的源文件哈希和北京 qwen-plus 配置；变更后需要按影响复验，不把动态模型的一次结果当成长期保证。

### 最终真实输出

| 新会话输入 | 意图 | 模型回复 | 请求数 |
|---|---|---|---|
| 你好，我是来问路的。 | talk | 问路？这附近没什么人来。你想去哪？ | 1 |
| 我想看看台灯底座。 | inspect | 台灯底座刻着“L-17”。 | 3 |
| 帮我看看那个。 | clarify | 哪个？ | 1 |
| 我把信封给你。 | clarify | 目前还不能接收信封。 | 1 |

台灯样例前两次请求没有 L-17，第二次使用 auto 收到服务端 inspect_object 请求；函数结果包含 L-17，经 tool_call_id 进入第三次 none 请求后，模型才生成上表回复。不是在提示词里预先提供刻字，也不是手工 JSON 伪造原生响应。

## 关键原理与限制

- **schema 不等于权限**：字符串 object_id 只说明语法。对象是否存在、是否同地、角色是否有权必须由可信程序判断，description 无法授予权限。
- **同名调用按 ID 配对**：两次 inspect_object 可以分别查台灯和信封，工具名相同但 call_id 不同；按名称存结果会覆盖或混淆。
- **wire_messages 与 history 分开**：前者承载本轮原生协议；后者维持 A01 的成对消息与旧存档契约。不保存工具消息意味着存档不能单独重放查询来源，轨迹也不自动成为长期发现记忆。
- **业务失败的自然语言有边界**：程序保证失败结果如实反馈、无隐藏正文泄露；提示要求承认未查到。脚本化测试验证信息流，真实抽样验证模型行为，没有通用语义判定器去证明任何最终文本都完全忠实。
- **范围保持 A02**：世界数据前后 deepcopy 对照证明只读；给物、移动、打开、事件账本留待 A03。多批查询、纠错重试、并行调度留待 A04。

## 交付文件与阅读入口

先读 [操作与代码阅读说明](a02_operation.md)，再顺着 tools → actions → model.complete → runtime → main 阅读代码。

新增：app/actions.py、app/tools.py、app/runtime.py、app/objects.json、tests/test_a02.py、scripts/a02_smoke.py。
本轮修改：app/model.py、app/main.py、README.md；复用现有 app/session.py / app/storage.py。
文档：本总结、操作说明、两份测试输出、真实演示证据。

## 协议核对来源

- [阿里云原生 Function Calling 流程](https://help.aliyun.com/zh/model-studio/qwen-function-calling)
- [Chat Completions 兼容参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
- [qwen-plus 地域能力](https://help.aliyun.com/zh/model-studio/qwen-plus)
- [qwen-plus-character 能力](https://help.aliyun.com/zh/model-studio/qwen-plus-character)

本次实现依据用户的完整任务卡、现有仓库与上述官方接口文档；任务卡中的 smolagents 设计对照未另行重读源码，不宣称本次完成了该框架的源码审阅，也未引入框架依赖。
