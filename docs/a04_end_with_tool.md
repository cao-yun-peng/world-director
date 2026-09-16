# A04：用终结工具结束 turn

日期：2026-09-16。协议：`a04-v6.1`。范围：本地课程原型。

## 1. 一句话理解

模型查询时调用查询工具，回答或行动时调用终结工具。程序根据工具调用控制循环，不再从普通正文解析决策 JSON。

```text
玩家：值班室有什么？
  → 模型调用 get_visible_scene()
  → 程序返回 lamp_01 / 台灯、envelope_01 / 信封
  → 模型调用 end_turn(reply="这里有一盏台灯和一个信封。")
  → 程序裁定并缓存本轮结果，向玩家显示 reply
```

这里是两次模型请求。旧版 `finish → 额外叙述` 的观察路径需要再请求一次；现在回答与结束信号都在终结工具中。
`tool_choice="required"` 要求模型从提供的工具中选择，包括 `end_turn`；不会强制它第一步就结束。

### a04-v6.1：查询需要服务于当前请求

真实试玩中，“你好”触发了目录查询、两个物品细节查询，再结束回复（run_id=6618fa58-9207-4d5c-8dd8-ac94ef6721b4）。
主 trace 确认三次模型请求、两个 inspect_object、最终版本 2；这说明协议执行成功，但查询选择超出了问候需要。

旧提示“目录没有提供时先查”缺少“当前请求确实需要查询”的前提，可能诱导模型每轮先探索。
a04-v6.1 明确：已有信息足够时直接 end_turn；缺少相关世界事实才查询；目录已经足够时不再逐件检查。
没有增加问候关键词分支、意图分类器或额外模型请求。tool_choice=required 仍允许直接调用 end_turn，不要求先查世界。

预期：普通问候一次 end_turn，1 次模型请求、0 个发现事件；询问目录才查目录，询问细节才查细节。
这是查询范围的提示调整，实际模型是否遵守仍需真实试玩；Fake 测试不能证明语义选择已经修好。

## 2. 顺着源码读

### 工具契约：app/turn_tools.py

| 工具 | 必填参数 | 含义 |
|---|---|---|
| get_visible_scene | 无 | 查询目录，继续循环 |
| inspect_object | object_id | 查询细节，继续循环 |
| end_turn | reply | 回复、澄清或总结观察，然后提交 |
| move | destination_id | 提交移动提议 |
| give | object_id、recipient_id | 提交给物提议 |

`TERMINAL_TOOLS` 集中定义三个终结工具及参数。参数均为非空字符串，不接受额外身份字段。
`parse_terminal_call` 将原生工具参数转换成已有 `ActionProposal`：`end_turn` 对应内部 `talk`，移动和给物仍保留各自动作类型。
回答与澄清共用 `end_turn(reply)`，模型不再需要填写 `kind` 和 `target_text=null`。

工具参数仍是 JSON，依然需要本地检查。此次删除的是正文决策 JSON 协议，不是删除输入校验。

### 查询与停止：app/async_runtime.py 的 decide

```python
# 解释用伪代码；预算和错误处理见实际源码。
for step in range(max_steps):
    calls = await request_tools(tool_choice="required")
    validate_whole_batch(calls)
    if is_terminal_call(calls):
        return parse_terminal_call(calls[0]), pending_discoveries
    results = await execute_readonly_queries(calls)
    append_tool_results(results)
```

实际实现里，终结工具参数错误也会作为原生 `tool` 结果反馈，下一步可修正或澄清；不会新开一个格式修复循环。
原始参数保留在模型 IO 中，主 trace 只记录工具名、调用 ID 摘要与固定错误码。

终结调用必须独占一个批次。`inspect_object + end_turn` 或 `give + end_turn` 同批返回时，整批在执行前被拒绝。
查询工具仍可在同批内并行，但依赖前一次查询结果的调用必须等下一步。

### 世界裁定：run_agent_turn → engine.commit_turn

```text
只读快照上的查询
  → 暂存成功发现
  → 合法终结调用形成 ActionProposal
  → 把发现与提议交给 commit_turn，一次裁定和接纳
  → 缓存确定性回执和历史
```

`give` 只表达意图。例如 `object_id="discovery-0"` 仍会被拒绝：这是知识记录编号，不是物品 ID。
业务裁定失败时，先前暂存的发现也回滚，结果为 `rejected`，可用同一个 `turn_id` 重放拒绝回执。

`end_turn` 直接展示经过提交的回复。移动、给物成功后的额外叙述暂时保留：它只读取程序回执，不再进入决策循环，失败时使用确定性回执。这个展示步骤仍计入同一请求与时间预算。

当前模型请求中的工具上下文只存在于本轮 wire，跨轮历史继续保存用户文字与最终回复；不会把没有配对结果的终结调用放进下一轮消息。

## 3. 错误、状态与 /retry

| 情况 | 行为 |
|---|---|
| 只返回普通文字或正文 JSON | `TOOL_CALL_REQUIRED`，不提交，也不猜测正文意思 |
| 终结调用与其他工具同批 | `TERMINAL_TOOL_CONFLICT`，本批不执行 |
| 工具参数非法 | 原生工具错误反馈，下一 step 可修正，原预算继续扣减 |
| 连续错误耗尽步数或请求 | 终止，丢弃暂存发现，不创建已提交回合 |
| 给物、移动被世界规则拒绝 | `rejected`，保留可重放回执，世界不变 |
| 查询失败后用 end_turn 澄清 | `completed` 表示回复已提交；失败查询仍在 trace，没有对应发现事件 |
| 未提交回合 /retry | 同一 turn_id、新 run_id，重新执行 |
| 已提交或业务拒绝回合 /retry | 同一 turn_id、新 run_id，0 次模型请求，返回缓存结果 |

超时、取消、原子提交和幂等沿用现有实现。结束回合不等于行动成功，`completed` 也不等于模型的每一句话都经过事实验证。

## 4. trace 怎么看

主文件仍为 `runs/a04.jsonl`，模型实际输入和原始输出仍通过 `io_ref` 对应 `runs/a04_io/...jsonl`。

1. 先核对 `run_started.runtime_version == "a04-v6.1"`。
2. 看模型 IO 输入：决策请求有五个工具、`tool_choice="required"`，不含 `response_format`。
3. 看输出：决策位于 `message.tool_calls`；参数在 `function.arguments`。
4. 看 `terminal_tool`：`validated` 只表示参数合法；`rejected / INVALID_ARGUMENTS` 表示参数检查失败。
5. 看 `commit`：确认业务 `completed` 或 `rejected`，以及实际世界版本。
6. 最后看 `run_finished`，区分未提交故障、正常提交和叙述回退。

trace_version 仍为 3，模型 IO 的存储格式没有变化。旧 JSON 决策诊断和 Schema 实验文档保留为历史证据。

## 5. 验证与边界

- A04 专项：61 项离线测试通过。
- 全课程：161 项离线测试通过，[完整输出](a04_end_tool_tests.txt)。旧正文语法分类测试已随旧协议移除，格式修复测试已改为终结工具行为测试。
- SDK + MockTransport：验证实际 HTTP 请求体与保存的输入一致，坏参数原文可追溯，反馈 call_id 配对正确，移动/给物后的叙述仍使用独立回执上下文。
- [九次离线互动](a04_end_tool/a04_demo.json)：共 14 次模拟模型请求，最终世界版本 6、事件 6；重复回合请求数为 0。
- [六类故障演示](a04_end_tool/a04_scenarios.json)：包含正常、坏参数、越权查询、等待超时、循环耗尽、重复提交；非正常查询/未提交路径均未改变世界。查询失败后据实回复的回合显示 completed。

未发送真实模型请求；不宣称 qwen3.7-plus 的真实服务稳定性已经通过。Function Calling、required 参数的服务端支持与中文回复质量仍需真实试玩。
未新增玩家身份绑定；玩家没有明确对应角色 ID 时应澄清，不能自动当作 other_npc。

## 6. 运行与练习

```powershell
# 离线，产物写到 docs/a04_end_tool，旧演示证据保留。
.\.venv\Scripts\python.exe -X utf8 -m scripts.a04_demo
.\.venv\Scripts\python.exe -X utf8 -m scripts.a04_faults

# 真实试玩：使用已配置的 .env，产生模型用量。
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine loop --max-model-requests 48
```

读完后自己回答：为什么工具参数合法还不能直接说“信已交付”？为什么把查询和 end_turn 放在同一批不能保证答复依据查询结果？为什么 `/retry` 不会重复转移信封？

参考：[smolagents 的最终答案工具与循环](https://github.com/huggingface/smolagents/blob/main/src/smolagents/agents.py)、[阿里云 Function Calling 参数](https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling)。本项目仅借鉴结束协议，没有引入新框架。
