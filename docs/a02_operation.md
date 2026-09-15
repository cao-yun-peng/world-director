# A02 操作与代码阅读说明

## 1. 准备

在项目根目录用 PowerShell 执行。现有依赖足够，无新增第三方库。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_a02.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

上述测试不访问模型服务、不读取真实密钥。SDK 测试使用 httpx.MockTransport；脚本化模型仅用于验证程序分支，不代表真实模型能正确理解全部表达。

## 2. 启动工具模式

沿用现有 `.env` 中的 Key 和服务地址，只在当前终端覆盖模型：

```powershell
$env:LLM_MODEL = 'qwen-plus'
.\.venv\Scripts\python.exe -m app.main --engine tools
```

模型说明：[qwen-plus](https://help.aliyun.com/zh/model-studio/qwen-plus)、[角色模型能力](https://help.aliyun.com/zh/model-studio/qwen-plus-character)。本次以北京兼容接口为目标；其他模型/地域需核对能力。模型设为 `qwen-plus-character` 或缺少 Key 时，工具模式会明确退出。

依次体验：

```text
你好，我是来问路的。
我想看看台灯底座。
/save saves/a02.json
/exit
```

重新启动一局，再输入 `帮我看看那个。`；这样没有前一轮“台灯”的明确指代。另可输入 `我把信封给你。`，应说明当前不支持给物，不应声称收下信封。

CLI 成功后显示意图、本轮请求数和剩余预算。查看整个场景可输入 `看看周围有什么。`。

## 3. 保存与恢复

```powershell
.\.venv\Scripts\python.exe -m app.main --engine tools --load saves/a02.json
# 也可加载真实存在的 A01 存档
.\.venv\Scripts\python.exe -m app.main --engine tools --load saves/a01.json
```

`/save 路径` 显式保存，退出不自动保存。存档仍为 schema_version=1、prompt_version=a01-v1；沿用原目标，只保存成对 user/assistant。模型请求失败时退出且不提交半轮；已有磁盘存档不受影响，之前未手动保存的内存回合不会自动落盘。

默认入口仍为 A01：

```powershell
.\.venv\Scripts\python.exe -m app.main --engine dialogue
```

## 4. 请求预算和错误

工具模式默认每个进程最多 12 次模型请求，可显式设置：

```powershell
.\.venv\Scripts\python.exe -m app.main --engine tools --max-model-requests 6
```

谈话/澄清通常 1 次；观察最多 3 次，每批最多 2 个顺序只读工具。预算耗尽可先 `/save`，再 `/exit`；如果继续提问，将产生 BUDGET_EXCEEDED 并结束。它是每进程请求计数，重启重新计数，不是账户费用上限。

| 错误 | 含义与处理 |
|---|---|
| INVALID_ACTION | 提议字段或 JSON 不合法；检查模型能力与提示词 |
| INVALID_JSON / INVALID_ARGUMENTS | 工具参数被拒绝，同 ID 错误交回模型 |
| UNKNOWN_TOOL | 非白名单工具被拒绝 |
| OBJECT_UNAVAILABLE | 当前不能查看，不透露不存在、异地或无权限的具体原因 |
| TOOL_ERROR | 本地工具异常，原始异常不发给模型 |
| MODEL_PROTOCOL_ERROR | 调用外壳、ID、批次数量或响应协议不合法；整轮终止 |
| TOOL_CALL_REQUIRED | 观察分支没有收到原生 tool_calls；不伪造工具调用 |
| UNEXPECTED_TOOL_CALLS | 意图或最终文本阶段仍返回工具调用 |
| EMPTY_MODEL_TEXT / MODEL_OUTPUT_TRUNCATED | 空回答或输出截断；不提交历史 |
| MODEL_REQUEST_FAILED / BUDGET_EXCEEDED | 网络/服务失败或预算耗尽；不自动重试 |

## 5. 轨迹与真实演示

常规工具模式写入 `runs/a02.jsonl`，包含模式、模型、会话 ID、意图、usage、调用 ID、工具请求、允许输出的结果及终止原因。失败请求的原始参数与未知工具名脱敏；不保存玩家原话和最终回复。身份不匹配在模型与文件操作之前拒绝，不写本轮轨迹。

需要真实模型证据时运行：

```powershell
$env:LLM_MODEL = 'qwen-plus'
.\.venv\Scripts\python.exe -m scripts.a02_smoke
```

这个脚本会实际向已配置模型服务发送请求并产生用量：四个固定教学输入，各自新建会话，最多 12 次请求，首个失败即停止。它不加载旧存档，不重试，不强制选择工具。输出追加到 `runs/a02_smoke.jsonl`，保留实际适配器请求/响应、tool_call_id、最终回复及 usage，不记录凭证。再次运行会产生新的请求，不会覆盖旧证据。

查看台灯案例时，应同时看到：

1. 前两次请求没有 `L-17`；第二次使用 `tools` 和 `tool_choice=auto`。
2. 服务端返回 `assistant.tool_calls`，名字为 inspect_object，参数指向 lamp_01。
3. 第三次请求中的 `tool` 消息包含函数返回的 L-17，ID 与原调用匹配。
4. 第三次使用 `tool_choice=none`，模型生成最终角色回复。

脚本记录的是适配器边界，SDK 的实际 HTTP 序列化由 MockTransport 测试验证。不要把测试替身或本说明中的例子当成真实调用记录。自动检查能确认分支与工具数据流；角色表达是否自然、澄清是否恰当仍需阅读真实输出。

## 6. 按调用链阅读代码

| 文件 | 职责 |
|---|---|
| app/objects.json | 原样复制的教学后台数据，完整数据不发送模型 |
| app/tools.py | 共享可访问规则、两个只读函数、schema、整批 ID 校验、显式白名单分发 |
| app/actions.py | 冻结的 ActionProposal 数据类与三个字段的本地约束 |
| app/model.py | 保留 generate；complete 返回消息、finish_reason 和 usage |
| app/runtime.py | 一次提议、一次工具批次、一次反馈；最后才构造新历史 |
| app/main.py | 可信身份来源、引擎选择、预算、存档命令与日志 |
| tests/test_a02.py | 业务边界、原生协议、旧存档接入、SDK 与脚本离线验证 |
| scripts/a02_smoke.py | 有预算的真实教学验收，独立于默认单元测试 |

session.py / storage.py 已有的可信身份参数直接复用；本次没有重新改写它们。

## 7. 扩展时守住什么

加教学物品：在 objects.json 补齐地点与权限元数据；目录和观察工具自动复用同一筛选规则。不要把完整描述添加进提示词。

加只读工具：明确新增函数、schema、白名单和参数规则，再加一个真实行为测试。本阶段只有两个工具，用显式分支更容易学习，暂不建通用 schema 引擎。

A03 再考虑移动、给物和世界账本；A04 再考虑多批查询和重试。现在的角色回复和行动提议都不代表世界事件。

schema 只告诉模型怎样请求，不能证明对象存在、角色有权或仍在同一地点；这些由程序检查。同名工具可能调用两次，必须按唯一 call_id 配对。wire_messages 暂存原生协议，避免破坏 A01 成对历史约定；存档本身因此不能重放工具来源，需要配合独立轨迹。
