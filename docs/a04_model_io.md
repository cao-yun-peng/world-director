# A04：从 trace 找到模型实际输入输出

日期：2026-09-16。运行协议 `a04-v4`，主 trace 与正文文件的 `trace_version=3`。
用户已授权保存输入输出；此功能用于 A04 `--engine loop` 的决策与叙述请求。

## 直接使用

退出旧 CLI，再运行原命令即可，不需要新增参数：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine loop --max-model-requests 48
```

启动时应显示 `a04-v4` 以及两处路径：

```text
E:\project\self-agent\world-director\runs\a04.jsonl
E:\project\self-agent\world-director\runs\a04_io\
```

旧进程退出后，内存世界会重置。旧日志不会自动补齐输入输出。

## 文件怎么关联

```text
runs/
├── a04.jsonl                               主 trace：步骤、状态、错误、io_ref
└── a04_io/
    └── <run_id>/
        ├── <模型调用 span_id>-attempt-1.jsonl
        └── <模型调用 span_id>-attempt-2.jsonl  仅发生传输重试时出现
```

主 trace 的 `model_request`、`model`、`narration_request`、`narration` 记录带有 `io_ref`。
这是相对于主 trace 所在目录的路径，例如：

```json
{
  "kind": "model",
  "step_id": 1,
  "attempt": 1,
  "io_ref": "a04_io/<run_id>/<span_id>-attempt-1.jsonl",
  "io_write_failed": false
}
```

将 `io_ref` 接在 `runs/` 后面即可打开正文文件。每个正文记录也有完整的
session_id、turn_id、run_id、step_id、span_id、attempt，可以反向关联。

### 一份正文文件里有什么

每行是一个 JSON 对象，按发生顺序追加：

| phase | 保存时刻 | payload 内容 |
|---|---|---|
| input | 调用前 | 完整 messages、工具定义、tool_choice、response_format，以及真实适配器的 model、max_tokens、stream、extra_body 等参数 |
| output | 模型返回后、决策解析前 | SDK 响应对象、未修改的回复字符串、工具调用与参数、结束原因、用量、服务端请求标识（若有） |
| end | 本次尝试结束 | 本次调用状态和错误码 |

`model_request / prepared` 表示输入已准备并尝试保存，不能单独证明网络请求已经发送。
最终 `model` 记录中的 model_requests 是实际尝试发送的计数。

真实响应的 `output.payload.format=provider_completion`，内容在
`output.payload.response.choices[0].message.content`。
离线 Fake 的 format 为 `adapter_completion`，内容在 `response.message.content`。

JSON 字符串未闭合、额外代码块等错误会原样保留在 content 中；外层文件仍然是有效 JSONL。
读取后能得到原始字符串，不能直接把日志中的转义字符当成模型多输出了字符。

`input.payload.extra_body` 是传入 SDK 的参数；SDK 会把它合并到 HTTP JSON 顶层。
测试已将两者规范化后逐项比较。响应保存的是 SDK 解码后的响应对象，
不是逐字节 HTTP 抓包；模型回复字符串不进行补全、裁剪或改写。

## 用 PowerShell 找最近一次模型调用

在项目目录执行：

```powershell
$tracePath = '.\runs\a04.jsonl'
$call = Get-Content $tracePath -Encoding utf8 |
    ForEach-Object { $_ | ConvertFrom-Json } |
    Where-Object { $_.kind -eq 'model' -and $_.io_ref } |
    Select-Object -Last 1

$ioPath = Join-Path (Split-Path $tracePath) $call.io_ref
$io = Get-Content $ioPath -Encoding utf8 |
    ForEach-Object { $_ | ConvertFrom-Json }

# 完整输入
($io | Where-Object { $_.phase -eq 'input' }).payload | ConvertTo-Json -Depth 50

# 真实模型的原始回复字符串
($io | Where-Object { $_.phase -eq 'output' }).payload.response.choices[0].message.content
```

指定某次运行时，在筛选条件中增加 `$_.run_id -eq '你的 run_id'`。
若最新一次是超时或取消，可能没有 output；这时读 input 与 end。
若进程被强制杀死，可能只来得及写 input，连 end 也不存在。

## 顺着源码理解

1. `app/async_runtime.py` 将决策和叙述调用都交给 `RunBudget.call_model`。
2. `app/execution.py` 的 `call_model` 复制当前 messages/options，并合并真实适配器的默认参数。
3. `call` 在每个 attempt 内先写 input，再发请求；拿到返回值后立即写 output，最后写 end。
4. `app/model.py` 保留 SDK 的响应对象和请求标识，同时提供运行时原先使用的 message/finish_reason/usage。
5. `app/trace.py` 负责建立 io_ref 和追加 JSONL。主 trace 继续只放摘要及关联路径。

每次网络重试使用同一 span 的不同 attempt 文件。
格式纠正属于下一 step 的新调用，保存包含纠正提示的那一次实际输入。
工具返回出现在下一次模型请求的 messages 中；原生 tool_call_id 保持原值。
叙述请求保存它实际收到的程序回执，不把早期未裁定上下文拼进去。
已提交回合的 `/retry` 不发模型请求，也不新建输入输出文件。

## 记录范围与失败处理

- 主 trace 不嵌入正文；`runs/*_io/` 已加入 Git 忽略。
- 正文文件按原文保存本次模型可见的提示、对话与工具结果，不做自动内容脱敏。
- 不记录客户端鉴权配置、Authorization 请求头或完整服务 URL。模型返回的服务端请求标识只放入正文记录。
- 网络异常、取消或 SDK 未产生可用响应对象时，没有 output；保留 input 和可获得的终止信息，不记录原始 HTTP 错误响应体。
- SDK 返回空 choices 时仍保存该响应，然后按 MODEL_PROTOCOL_ERROR 拒绝。
- 正文写入失败会设置 `io_write_failed=true` 和整轮 `trace_write_failed=true`，CLI 提示日志写入失败；日志失败不会触发额外模型重试或重复世界提交。
- 通过 Python 调用 `run_agent_turn` 且不传 trace_path 时，不创建日志或正文文件。
- A01—A03 的同步入口及直接调用适配器的独立脚本未接入这份 A04 关联日志。

## 验证

新增 8 项离线测试，包含真实 SDK + MockTransport 的请求体一致性验证。
覆盖格式纠正原文、工具结果/叙述上下文、网络重试、超时、取消、正文写入失败、幂等重放与空 choices。
全量 161 项测试通过，结果见 `a04_model_io_tests.txt`。本次没有新增真实模型调用。

此前真实服务已复现的格式问题仍可能发生；本次完成的是保存证据与关联排查。
