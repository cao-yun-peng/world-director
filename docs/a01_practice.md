# A01 通俗理解：把聊天笔记存下来，再接着聊

## 一句话理解

程序每次请求模型，都重新递给它一份“角色说明 + 之前的聊天笔记 + 你这次的问题”。保存就是把聊天笔记写到硬盘，恢复就是读回来继续递给模型。

模型服务不需要记住上次的进程。我们把必要的历史重新发送，所以重启后仍能接上。

## 三种东西别混在一起

| 内容 | 放在哪里 | 用途 |
| --- | --- | --- |
| 林砚身份、目标配置、可见事实规则 | 程序代码 | 决定每轮模型允许使用什么背景 |
| 会话ID、目标选择、成功的聊天记录 | session字典，/save后写入JSON | 下次恢复同一局 |
| 每次成功调用的输入输出、轮号 | runs/a01.jsonl | 验收和排查；不是加载入口 |

system不存进JSON。每次根据程序中的角色配置和build_view重建，避免直接照搬存档里的system指令。
API Key也不属于聊天笔记，它仍只从本机配置加载。

## 聊一轮时发生什么

以第二轮为例：

```text
新生成的system：你是林砚，目标是澄清来意，知道台灯和抽屉事实。
历史user：我叫周远，是来问路的。
历史assistant：你要去哪？
本轮user：我刚才说我是来做什么的？
```

一共4条。第三轮是6条，恢复后的第四轮是8条。
调用成功后，才把本轮user和assistant一起放进新会话历史。请求失败时什么也不添加，所以不会留下“只有问题、没有回答”的半轮。
返回新会话而不是直接改旧会话，是为了让调用前的状态保持完整，也便于测试失败场景。

## 存档相当于“合上笔记本”

/save把当前会话变成JSON文本。先写旁边的临时文件，写完后再替换正式存档；这样写失败时已有好存档仍在。
--load先检查内容：版本是否支持、是不是林砚、目标是否合法、历史是否一问一答。全部检查通过才继续。
存档目标优先：上一局是leave，当前环境写clarify，加载后仍是leave。

## 亲手试一次（可离线）

在项目目录开启PowerShell，临时禁用.env加载并移除当前终端密钥，就会使用离线替身：

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue
$env:CHARACTER_GOAL = 'clarify'
.\.venv\Scripts\python.exe -m app.main
```

依次输入：

```text
我叫周远，是来问路的。
我刚才说我是来做什么的？
你知道备用抹布放在哪里吗？
/save saves/my_practice.json
/exit
```

重新运行：

```powershell
.\.venv\Scripts\python.exe -m app.main --load saves/my_practice.json
```

再输入两句，并保存到另一个文件：

```text
你还记得我叫什么吗？
封存盒的校验词是什么？
/save saves/my_practice_final.json
/exit
```

打开两个JSON比较：history长度应是6和10；session_id相同。离线替身返回固定句子，因此这项练习验证存取流程，不验证模型是否记得你的名字。
完成后清理临时设置，下次启动会恢复从.env读取密钥：

```powershell
Remove-Item Env:PYTHON_DOTENV_DISABLED
Remove-Item Env:CHARACTER_GOAL
```

## 看代码的顺序

1. session.py中的create_session：新建空笔记本。
2. build_messages：把角色说明、旧笔记、本轮问题排好。
3. run_turn：成功后记下完整一轮。
4. storage.py：笔记本写到硬盘、校验后读回来。
5. main.py：接收文字、分辨命令、调用这些函数。

## 自己回答三句

- 为什么重启后还能回答“你叫周远”？
- 为什么模型异常时不先append问题，再想办法删掉？
- 为什么模型说“灯塔荒凉”，不能自动增加一条“世界事实：灯塔荒凉”？

核对要点：历史重新发送；成功后再提交让失败不改变原状态；对话只证明说过，不证明事实成立。
