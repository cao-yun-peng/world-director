# AI 互动世界导演 · D001

第一天：让林砚在 CLI 中回答玩家的一句话。Python 3.10+。

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
选型依据：[模型说明](https://help.aliyun.com/zh/model-studio/qwen-plus-character)。这是基于能力定位的选择，尚未实测角色效果。

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

这里只读环境变量，不自动加载 `.env`。这些设置仅影响当前终端及其子进程。
有密钥但显式配置为空或请求失败时会报错退出，不会自动改用假回复。

再次验证无密钥路径：

```powershell
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
python -m app.main
```

每次成功的一问一答追加到 `runs/d001.jsonl`，仅包含 `day`、`mode`、`model`、`input`、`output`。
真实回复标记 `real`，离线回复标记 `fake`；失败或空输入不写成功记录。
运行记录留在本地并被 Git 忽略，分享前检查对话内容是否包含敏感信息。

## 阅读顺序

1. `app/model.py`：`ModelAdapter` 用 `Protocol` 约定 `generate(messages) -> str`；真实模型和离线替身分别实现它。
2. `app/main.py`：读取配置、选择模型、构造独立的 system/user 消息、调用、记录。
3. `exercises/count_calls.py`：独立的字典练习，不接入对话流程。

后续换模型服务时实现同一个接口即可；角色调用不需要了解 SDK。
今天没有多轮循环、记忆、工具调用或世界状态。

## 验证与基础训练

```powershell
python -m unittest discover -s tests -v
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
