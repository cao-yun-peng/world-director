# AI 互动世界导演 · A03

新增世界账本、移动与给物裁定、个人发现、事件因果、回合去重及叙述失败回退。
默认仍运行 A01 对话；A03 使用独立内存世界。

```powershell
.\.venv\Scripts\python.exe -m scripts.a03_demo
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# 使用本机已配置且支持 Function Calling 的模型：
.\.venv\Scripts\python.exe -m app.main --engine world
```

- [A03 分步教学与练习（D015—D021）](docs/a03_practice.md)
- [A03 结果、边界与验收证据](docs/a03_results.md)
- [A03 连续故事的实际状态与事件](docs/a03_demo.json)
- [Generative Agents 源码阅读笔记](docs/a03_source_notes.md)

世界模式中 /retry 重发上一回合；/exit 退出。**本课没有世界存档**，
世界模式不支持 /save 或 --load；对话 JSON 不能恢复物品归属、事件和去重记录。
保证范围为单进程、顺序调用；自动测试不等于真实模型的自由叙述忠实性评估。

## A02 原生只读工具

新增意图理解与原生只读工具调用：talk / inspect / clarify。默认仍运行 A01 对话模式。

```powershell
$env:LLM_MODEL = 'qwen-plus'
.\.venv\Scripts\python.exe -m app.main --engine tools
```

- [A02 实现总结与验收证据](docs/a02_results.md)
- [A02 操作和代码阅读说明](docs/a02_operation.md)
- [A02 专项测试输出](docs/a02_tests.txt)
- [全部回归输出](docs/a02_all_tests.txt)

## A01 基础功能

当前版本支持多轮会话、JSON保存及跨进程恢复。Python 3.11+。

## 新建、保存、恢复

```powershell
.\.venv\Scripts\python.exe -m app.main
# 交互中：/save saves/my_session.json，/exit
.\.venv\Scripts\python.exe -m app.main --load saves/my_session.json
```

退出不自动保存；加载沿用存档目标。详情见[验收报告](docs/a01_results.md)和[通俗实践指南](docs/a01_practice.md)。

## D004：让测试看见模型输入

新增 `tests/test_d004.py`，用记录型假模型测试输入快照和完整 CLI。该测试现已适配多轮CLI，无需真实 API。

- [动手实践与学习指南](docs/d004_practice.md)：复写、预测、制造错误、恢复、检查CLI输入。
- [实验结果](docs/d004_results.md)：首次通过 → 浅复制断言失败 → 恢复通过 → 完整21项回归。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_d004.py" -v
```

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
它不支持 Function Calling；A02 工具模式请显式切换为 qwen-plus，详见上方操作说明。
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

### 角色可见视图

`app/main.py` 的 `ACTOR_ID = "lin_yan"` 固定当前身份；玩家原话和环境变量不能切换这个身份。
`build_view(FACTS, ACTOR_ID)` 先筛选，再将结果传给 `build_prompt(card, visible_facts)`：

- 已登记角色可见 public 事实；private 事实必须有包含该角色的有效字符串列表。
- 缺失或未知可见性、无效私有名单不放行；未登记角色抛出 ValueError。
- 返回新建的 `id/text` 字典，不带后台备注、权限字段，也不与后台字典共享。
- `other_npc` 仅为权限测试身份，没有运行第二个角色。

每轮成功回复追加到 `runs/a01.jsonl`，旧日志保留；存档位于本地 `saves/`。
日志保留 `day`、`mode`、`model`、`input`、`output`、`goal_id`、`goal`、`prompt_version`（`a01-v1`），包含 `actor_id`、`visible_fact_ids`、`session_id`、`turn_index`。不记录全量后台资料。
A01演示共5次真实调用，固定 clarify，`max_tokens=256`、`max_retries=0`，同一会话逐轮携带成功历史。
每条普通输入产生一次调用，/save与/exit不调用模型；不提供持久化预算系统。
真实回复标记 `real`，离线回复标记 `fake`；失败或空输入不写成功记录。
运行记录留在本地并被 Git 忽略，分享前检查对话内容是否包含敏感信息。

## 阅读顺序

1. `app/scene_data.py`：教学后台数据与已登记角色 ID，里面的校验词均为虚构测试材料。
2. `app/view.py`：根据权限生成只含 `id/text` 的角色视图。
3. `app/character.py`：保留身份、风格和目标，追加“已知事实”区域。
4. `app/main.py`：固定身份 → 构造视图 → system/user 消息 → 调用与日志。
5. `app/model.py`：沿用 D002 的 `ModelAdapter` 与真实/离线实现。

后续换模型服务时实现同一个接口即可；角色调用不需要了解 SDK。
A01 使用会话历史；A02 已增加原生只读工具，不增加长期记忆或权威世界状态更新。视图控制输入信息，不保证模型不会生成无依据的内容。

## D003 验收材料

- [完整实验与 CLI 消息捕获](docs/d003_results.md)
- [完整测试输出](docs/d003_tests.txt)
- [两数之和手算与独立解释核对](docs/d003_practice.md)

测试保持 `.env` 隔离，不读取真实密钥。D003 验证了两个主体的权限、异常权限拒绝、字段裁剪、返回字典独立性，以及玩家自称其他角色时的完整模型输入隔离。

## 测试与往日基础训练

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
- **角色说地下室有信，是否就是真实世界事实？** 不是。A01 只有角色生成的语言；A03 已增加权威世界账本，但依然只有程序裁定能改变事实，不能把一句回复当作事实变更。

API Key 是访问凭证，写入代码可能经 Git 历史或分享泄露。环境变量让凭证与代码分开，日志也不保存凭证或 SDK 请求。
