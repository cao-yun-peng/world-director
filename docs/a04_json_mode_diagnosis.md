# A04：重复 INVALID_DECISION 的参数对照定位

日期：2026-09-16。范围：诊断，业务代码未修改。证据沿用 docs/a04_*；L0 本地教学实验，不作生产验收结论。

## 结论

当前 qwen-plus 请求中的 JSON mode 是已找到的触发条件：在完全相同的提示、历史与工具定义下，
只删除 `response_format={"type":"json_object"}`，本次对照就返回了合法的原生 `get_visible_scene({})` 调用。
保留 JSON mode 的基线、换 seed 和去历史三组都返回了非对象 JSON。

这是当前模型服务、提示和调用配置的实证结果；不足以宣称所有千问版本的 tools 与 JSON mode 都不兼容，
也没有证据断定服务端内部是哪项解码实现导致该现象。

## 两次用户运行

- 原失败 run：`04284b47-7ce3-4706-a6e2-2f7c57bf75d6`。
- /retry run：`247a684d-0d3c-4453-adee-ea5a6d933d52`。
- 两者完整请求逐项相等，规范化 JSON SHA-256 均为 `b2f83dec702058a9019657dc904d8cd7a0afd9a4dae18c16fdf3577e9cbe8c77`。
- 响应均为 content 字符串 `8`，没有 tool_calls，finish_reason=stop，completion_tokens=1。
- 两次服务端请求标识不同，CLI 重新请求了模型，没有走已提交回合的结果重放。
- 本地 json.loads 得到 int；parse_action 要求对象，因此拒绝。not_object 不在现有一次格式纠正范围内。

## 排除本地请求组装问题

使用真实 SDK 和离线 MockTransport 重放请求组装，未联网：

- SDK 发出的请求体与日志输入一致（extra_body 合并到 HTTP JSON 顶层后比较）。
- response_format=json_object、tool_choice=auto、两份工具定义均被发送。
- enable_thinking=false，max_tokens=512，没有显式 seed 或 stop 参数。
- 将日志响应交回适配器，content 仍为 `8`，没有被适配器改写。

## 六次真实对照

用户明确批准最多 6 次，实际发送 6 次。SDK 与运行时均只尝试一次，不执行任何返回的工具，不提交世界。
原文在 `runs/a04_ablation_io/`；主轨迹 `runs/a04_ablation.jsonl`；结果索引 `runs/a04_ablation_summary.jsonl`。

| profile | 与原请求的差异 | 实际输出 | 本地结果 |
|---|---|---|---|
| baseline | 无 | JSON 字符串 `"profile_id"` | not_object |
| without_json_mode | 仅移除 response_format | 原生 get_visible_scene，arguments={}，finish_reason=tool_calls | 工具调用结构通过校验，未执行 |
| without_tools | 移除 tools 和 tool_choice | `{"kind":"inspect_object","object_id":"duty_room"}` | unknown_kind |
| different_seed | seed=1235 | `8` | not_object |
| without_history | messages 只留 system 和最后一个 user | `["$DUTY_ROOM_INSPECT_RESULT$"]` | not_object |
| forced_scene_without_json | 移除 response_format，强制 get_visible_scene | 原生 get_visible_scene，arguments={}，finish_reason=stop | MODEL_PROTOCOL_ERROR |

最后一组是两项参数改变的补充兼容性检查，不当作单变量因果证据。
它另行暴露出：服务端强制工具调用会在本次响应中同时给出 tool_calls 与 stop，
当前 validate_completion 仅接受 tool_calls 对应 finish_reason=tool_calls，因而拒绝。
这不是原用户 run 的直接原因，原用户 run 没有 tool_calls。

对工具结果做离线结构检查时，先按实际适配器保留 id/type/function，再验证；
不能把 SDK 原始响应的额外 index 字段直接拿去跑应用层严格校验。

## 为什么原样 /retry 没有修复

失败回合不会提交对话历史；/retry 复用同一用户文本和 turn_id，而错误反馈没有保存在下一次运行的输入中。
所以新的 run 又发送了原配置和原提示。本次两次请求的完整相等已由日志确认。

官方文档说明 seed 的默认行为有利于重复结果，但这不是本次唯一原因：
显式换 seed 仍失败，而且本次基线复跑返回了另一个错误值 `"profile_id"`。
不能把“改随机种子”当作修复。

## 后续修复方向（本次未实施）

1. 工具选择阶段保留原生 tools，移除文本 JSON mode；先回归同一失败对话。
2. 若最终决策需要服务端结构化约束，应设计单独的无工具决策请求，仍计入同一请求/时间预算；不能仅靠提示里要求 JSON 就宣称结构稳定。
3. 对不含可执行行动的非对象 JSON，可评估纳入一次有限格式纠正；仍不把数字或数组猜成行动。
4. 单独验证工具调用结束原因的供应商兼容性，再决定归一化规则，不无条件放行异常消息。

本次只有一组仅移除 JSON mode 的成功样本，尚未验证修复后的多轮查询、终态决策和世界提交。

## 官方参考

- [千问 Chat Completions 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)：seed、response_format、tool_choice。
- [结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)：JSON Object 与 JSON Schema 的约束范围不同。

官方资料用于核对参数含义；本次兼容性结论来自保存的真实对照，不声称官方已经确认该故障。

## 补充检查：qwen3.7-plus 工具调用后的纯文本输出

日期：2026-09-16。会话：`38d7a3b9-05af-44ef-9000-a11ad64caf73`。
本次仅检查用户现有真实运行并离线回放；新增外部模型请求 0 次，业务代码未修改。

### 失败回合：0bfcf075-b6df-4e24-83e9-5df73015f840

三次请求及服务端响应均标记模型 `qwen3.7-plus`，模型切换已生效。
三次输入均包含 tools、tool_choice=auto、response_format=json_object 和 enable_thinking=false。

1. 第一次请求返回原生 `inspect_object({"object_id":"lamp_01"})`，工具成功返回“一盏台灯，灯座底部刻着 L-17。”。
2. 第二次请求返回纯文本“台灯灯座底部刻着 L-17，下面没有藏东西。”；finish_reason=stop，没有 tool_calls。`parse_decision` 对正文执行 json.loads，在第 1 行、第 1 列失败。
3. 程序追加一次格式纠正提示后，第三次请求仍返回纯文本“台灯底座下刻着‘L-17’，除此之外没有发现其他东西。”（原始标点以 IO 文件为准）；再次解析失败，终止为 INVALID_DECISION。

所以请求 3 次的含义是一次工具决策、一次终态决策、一次格式纠正；不是三次查询或网络重试。
没有提交发现、行动或失败回合的对话；世界版本和事件数仍为 0。

### 上一回合的 completed 也使用了纠正

`aca3edac-033c-49aa-a068-337787c054e9`：get_visible_scene 成功后，模型先输出纯文本“现在房间里有一盏台灯和一个信封。”；
格式纠正后才输出合法的 talk JSON。因此 completed 只能说明最终通过，不表示首次输出已遵守决策协议。

### 独立的内容准确性问题

工具回执只提供灯座刻字，没有说明台灯下面是否藏有物品。“下面没有藏东西”超出了工具提供的信息。
即使将这段文本包进合法 JSON，也不能认为内容准确；未提供信息应表达为无法确认。
另外，失败回合首次输入没有 lamp_01 的目录映射，但模型直接选中了该 ID；服务器端仍通过实际可见性检查。
这表明提示中的“无目录先查目录”也没有被完整遵循，不能只根据一次工具调用成功推断模型遵守了所有规则。

### 离线复核与结论边界

使用真实 OpenAI SDK + httpx.MockTransport 返回六份已保存响应，按现有适配器和运行时顺序回放两个回合。
六次 SDK HTTP 请求体均与保存输入逐项相等（extra_body 展开到顶层）；重现 completed 与 INVALID_DECISION，各 3 次请求、一次格式纠正。
失败回合前后世界状态完全相同。回放不访问网络，不使用真实 API Key，不向原运行日志写入新记录。

直接原因是工具结果之后的终态内容没有遵守 JSON 决策协议；当前模型与请求组合未提供预期的结构化约束效果。
本次没有新做参数消融，不能仅凭日志判定服务端内部为何未约束正文，也不能断言所有工具调用与 JSON mode 都不兼容。
仅更换模型尚未解决完整流程问题。此前列出的修复方向仍是待验证候选，不代表已实施或已通过验收；
遵循用户简洁代码要求，不继续增加错误形态枚举或文本补救分支。

原始证据：`runs/a04.jsonl` 第 135–160 行及对应 `io_ref`；六份 IO 路径、文件哈希与复核结果见 `a04_json_mode_evidence.json` 的 `qwen3_7_plus_followup`。

