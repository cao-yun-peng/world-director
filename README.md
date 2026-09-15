# AI 互动世界导演 · D002

第二天：让同一个林砚带着不同目标，回答玩家的一句话。Python 3.11+。

## 运行

在项目目录执行（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.main
```

未配置 `LLM_API_KEY` 时会显示 `[offline mode]`，返回带有“离线演示”的固定回复。
如果当前 Python 已安装依赖，也可以直接执行 `python -m app.main`。

### 接通真实模型

今天只实现一种协议：OpenAI 兼容的 Chat Completions。
默认模型为 `qwen-plus-character`，专门用于角色对话；官方说明其优化了人设遵循、话题推进和共情能力，适合今天的文字角色互动。
它目前不支持 Function Calling，后续工具调用阶段再通过适配层选择支持工具的模型。
选型依据：[模型说明](https://help.aliyun.com/zh/model-studio/qwen-plus-character)。D002 的实际对照见 `docs/d002_results.md`。

默认 base_url 为北京地域兼容地址 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
阿里云推荐使用业务空间专属域名，旧域名仍可用；若使用其他地域或专属业务空间，请覆盖环境变量，确保与密钥地域一致。
调用格式参考[百炼官方文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。

```powershell
# 隐藏输入密钥，避免把密钥直接写入命令历史。
$env:LLM_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'API Key' -AsSecureString)).Password
$env:LLM_MODEL = 'qwen-plus-character'
$env:LLM_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
python -m app.main
```

也可以在项目根目录的 `.env` 中配置（下面的密钥只是占位文字）：

```dotenv
LLM_API_KEY=替换为你的密钥
LLM_MODEL=qwen-plus-character
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

程序启动时通过 `python-dotenv` 加载项目根目录的 `.env`，再用 `os.getenv` 读取配置。
终端环境变量优先于 `.env`；模型名和地址不填写时使用代码默认值。
`.env` 已被 Git 忽略。PowerShell 中的临时环境变量仅影响当前终端及其子进程。
有密钥但显式配置为空或请求失败时会报错退出，不会自动改用假回复。

再次验证无密钥路径：

```powershell
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
# 暂时禁用 .env 加载，否则文件中的密钥会被重新加载。
$env:PYTHON_DOTENV_DISABLED = '1'
python -m app.main
Remove-Item Env:PYTHON_DOTENV_DISABLED
```

### 选择角色目标

`CHARACTER_GOAL` 默认是 `clarify`，也可写进 `.env`。未知值会在请求模型前报错。

```powershell
$env:CHARACTER_GOAL = 'clarify'
.\.venv\Scripts\python.exe -m app.main
$env:CHARACTER_GOAL = 'leave'
.\.venv\Scripts\python.exe -m app.main
Remove-Item Env:CHARACTER_GOAL
```

- `clarify`：先弄清玩家来意，再考虑是否建议其暂留。
- `leave`：礼貌地建议玩家暂时离开，可建议改日来访，不编造危险或强迫玩家。

每次新建角色卡，只替换目标；背景、性格和共同约束相同，玩家原话单独放在 user 消息。
目标表示角色想要的结果，不代表玩家已经行动。

每次成功的一问一答追加到 `runs/d002.jsonl`，D001 原始记录保留。
日志包含 `day`、`mode`、`model`、`input`、`output`，以及 `goal_id`、`goal`、`prompt_version`（`d002-v1`）。
本轮对照只做 4 次真实调用，`max_tokens=256`、`max_retries=0`，不共享历史。
CLI 每运行一次仍会产生一次调用；这不是一个持久化预算系统。
真实回复标记 `real`，离线回复标记 `fake`；失败或空输入不写成功记录。
运行记录留在本地并被 Git 忽略，分享前检查对话内容是否包含敏感信息。

## 阅读顺序

1. `app/character.py`：基础角色卡、目标映射和纯函数 `build_prompt()`。
2. `app/model.py`：`ModelAdapter` 用 `Protocol` 约定 `generate(messages) -> str`；真实模型和离线替身分别实现它。
3. `app/main.py`：读取配置、选择模型、构造独立的 system/user 消息、调用、记录。
4. `exercises/count_calls.py`：独立的字典练习，不接入对话流程。

后续换模型服务时实现同一个接口即可；角色调用不需要了解 SDK。
今天没有多轮循环、记忆、工具调用或世界状态。

## 验证与基础训练

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
python -m exercises.count_calls
```

练习输出：

```text
{'inspect_object': 3, 'talk': 2, 'move': 1}
{}
```

每个调用只处理一次，字典查询与更新平均 O(1)，总时间 O(n)，空间 O(k)，k 是不同工具数量。
自动测试使用替身验证程序行为，不能充当真实模型调用证据。

## 三个检查题

- **为什么保留 ModelAdapter？** 把角色对话和供应商 API 分开。更换供应商或用替身测试时，上层仍调用同一个方法。
- **为什么假回复不能标记 real？** 会伪造接通证据，掩盖配置或网络问题，也会让后续评测失去可信度。
- **角色说地下室有信，是否就是真实世界事实？** 不是。现在只有角色生成的语言，没有权威世界账本，不能把一句回复当作事实变更。

API Key 是访问凭证，写入代码可能经 Git 历史或分享泄露。环境变量让凭证与代码分开，日志也不保存凭证或 SDK 请求。
