# A04：重复格式故障与 trace 排查

> 后续更新：用户已授权保存模型输入输出。当前实现为 `a04-v4`、`trace_version=3`，主 trace 通过 `io_ref` 关联本地原文文件。下文的 `a04-v3` 和“不保存正文”是当时的历史状态；最新用法见 [模型输入输出记录](a04_model_io.md)。

日期：2026-09-16。运行协议 `a04-v3`，JSONL 轨迹版本 `2`。

## 这次日志确认了什么

会话 `589cc772-6607-4929-b28c-196d0faecbae` 的最后一轮：

`run_id=c32b1db9-79c2-4f58-bdb0-f4955356bcd8`

1. 第 1 步：服务返回成功，输出用量 31 tokens，本地 JSON 解析失败。
2. 第 2 步：收到格式纠正提示后，服务返回成功，输出用量 32 tokens，仍解析失败。
3. 本轮终止为 `INVALID_DECISION`，共 2 次请求，没有 commit，`committed_revision=null`。

这是一次初次决策加一次格式纠正，两步的 attempt 都是 1，不是网络重试。
前六轮每轮 1 次请求，因此进程预算从 48 变为 40，计数符合实际发送次数。
旧日志只有 `shape=invalid_json`，无法恢复旧响应的具体语法错误或正文。

## 新做的真实对照

运行 `scripts.a04_protocol_probe --real --max-model-requests 4`，实际完成 4 次请求。
使用脚本中固定的用户已展示角色对话（长串“啊”近似为 40 个），并非旧响应的逐字重放。
没有执行任何返回的工具调用，也没有世界提交。

| 样本 | 请求形式 | 结果 |
|---|---|---|
| 1 | tools + JSON | 合法 clarify 决策 |
| 2 | 仅 JSON | 合法 clarify 决策 |
| 3 | tools + JSON | 字符串未闭合 |
| 4 | 仅 JSON | 字符串未闭合 |

样本 3、4 的结构诊断均为：`content_length=74`、`line=1`、`column=46`、
`offset=45`、`leading_form=object`、`json_error=unterminated_string`。
响应经过 `validate_completion` 后才进行 JSON 解析，所以这两个样本的结束原因是 `stop`，
并非代码已识别的 `finish_reason=length`。

结论：真实服务偶发返回不完整的 JSON 字符串，两种参数组合都发生过。
不能将原因认定为 tools 与 JSON 参数冲突，也不能据此宣称真实服务已修复。
没有记录正文，因此无法逐字展示返回内容；新对照的语法错误也不能回填成旧 run 的已知事实。

此前“上任管理员突然离开”没有对应角色卡事实；前六轮没有工具查询记录。
`completed` 只表示协议和本地结算完成，不等于自由对白经过事实核验。
谈话本身不增加世界事件，世界版本 0 也不代表程序没有处理过对话。

## 新 trace 记录什么

默认文件：`E:\project\self-agent\world-director\runs\a04.jsonl`。
CLI 启动会显示实际路径与 `a04-v3`。退出并重新运行 CLI 才会加载更新；内存世界会重新开始。
旧记录不会自动补齐；新增记录带有 `trace_version=2`。

| 记录 | 排查信息 |
|---|---|
| run_started | runtime_version、配置的 model_name、provider_host、limits、history_messages |
| model / narration | 请求格式和工具选择；finish_reason、内容类型/长度、工具调用数量；原有 step、attempt、耗时、用量 |
| decision / error | JSON 错误类别、行列、字符偏移、开头类型，或缺失/非法字段的结构诊断；是否已用过纠正 |
| format_correction / scheduled | 计划在下一步纠正格式；关联失败 decision 的 span |
| decision / validated | 校验通过的决策类型，包括 talk、clarify、finish、move、give |
| tool / commit / run_finished | 原有工具执行、正式提交及最终终止原因 |

`line`、`column` 从 1 开始；`offset` 从 0 开始。
`unterminated_string` 的位置指字符串开始处，不一定是应该补引号的位置。
错误类别、已知结束原因等使用固定白名单；未知结束原因记为 `unknown`。
默认不保存模型正文、玩家输入、工具参数、密钥或含凭据的完整 URL。

### 顺着日志读一次失败

```text
model / ok                  收到了模型响应，尚未保证决策正确
  response_details.finish_reason = stop
decision / error            JSON 字符串未闭合
format_correction / scheduled
model / ok                  再请求一次，占用下一步和请求预算
decision / error            再次失败
run_finished / INVALID_DECISION
  committed_revision = null
```

网络临时失败则表现为同一 step 下 attempt 增加，出现 `retry_wait`。
格式纠正使用下一 step，出现 `format_correction`。两种重试现在能直接区分。
成功的 `decision / validated` 也不表示行动已完成，继续看 commit 与 run_finished。

### 按 run_id 筛选

```powershell
$runId = 'c32b1db9-79c2-4f58-bdb0-f4955356bcd8'
Get-Content .\runs\a04.jsonl -Encoding utf8 |
    ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.run_id -eq $runId } |
    ConvertTo-Json -Depth 10
```

## 验证与边界

新增 7 项离线测试，覆盖语法分类、有限对照、取消清理以及落盘 JSONL 的错误/纠正/成功/工具/叙述路径。
原有格式测试同步更新；全量测试结果见 `a04_trace_diagnostics_tests.txt`。
历史的 137 / 146 项测试结果仍保留，不能作为当前源码快照。

本次没有放宽决策校验，没有自动修补模型文本，没有增加格式纠正次数。
补充 trace 是为了定位故障，不能保证后续每个模型响应合法。
