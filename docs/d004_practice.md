# D004 动手实践：预测 → 制造错误 → 观察失败 → 恢复

本次代码已实现、实验已跑完。你可以用下面的步骤亲手重做；每一步都有明确的操作和检查点，不需要配置API。

## 1. 先认清三个容器（5分钟）

打开 tests/test_d004.py，只看 RecordingFakeModel。

| 表达式 | 含义 |
| --- | --- |
| messages | 调用者持有的本次消息列表 |
| calls | 记录器保存的多次调用列表 |
| calls[0] | 第一次调用的消息列表 |
| calls[0][0] | 第一次调用的第一条消息字典 |
| calls[0][0]["content"] | 那条消息当时的文字 |

在纸上画出“列表 → 字典 → 字符串”的关系，然后回答：如果只创建一个新列表，列表里的字典会自动变成新字典吗？

检查点：要独立的是本次消息列表和每个消息字典。当前字符串不可变，可以共享；给 content 赋新值是在修改字典的映射。

## 2. 亲手复写核心（10分钟）

暂时折叠现成的 generate 方法，先在纸上或空白编辑区用循环写出它，再和项目实现对照。保持签名和属性不变。

你的实现只需要依次做四件事：

1. 创建本次快照列表。
2. 遍历 messages，为每条消息创建新字典，放入快照。
3. 把完整快照加入 self.calls。
4. 返回 self.reply。

不要修改消息内容，也不要引入模型调用。

<details>
<summary>写完后展开：核对实现与适用范围</summary>

```python
snapshot = []
for message in messages:
    snapshot.append(message.copy())
self.calls.append(snapshot)
return self.reply
```

这里不是对任意复杂对象做深复制：message.copy() 仍是浅复制，只是当前字典的值全部是字符串，所以足够。
如果以后 content 变成包含可变字典或列表的结构，必须重新评估需要复制的层级；今天不提前增加这套逻辑。

</details>

## 3. 跑绿色版本，逐行读测试A（5分钟）

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_d004.py" -v
```

应看到两个测试都通过。接着在测试A中找出三个阶段：准备数据、调用记录器、修改原输入再断言。

解释每个检查发现什么：

- reply是否等于预设值：记录器是否返回固定回复。
- calls长度是否为1：是否只记录一次调用。
- calls[0]长度是否为1：是否受后来 append 影响。
- content是否仍是“你好”：内部字典是否被后来赋值影响。

不要只用 is not 判断列表不同；我们需要证明内容没有污染。

## 4. 先预测，再亲手制造浅复制错误（10分钟）

先填写下面三项，再动代码：

```text
我预计失败的测试名称：
我预计失败的断言：
我预计“实际值 / 期望值”分别是：
```

在 generate 中，把创建 snapshot 到 self.calls.append(snapshot) 的四行临时替换为：

```python
self.calls.append(messages.copy())
```

保留 return self.reply 和所有断言。再次运行上面的聚焦测试命令。

检查点：应当是1条测试失败，而不是 ImportError、SyntaxError 或找不到测试。先自己读失败栈，找到断言位置和实际值。

<details>
<summary>观察后展开：为什么A失败、B仍通过？</summary>

A失败：第一个字典仍共享，记录中的content变成“后来修改的内容”。外层列表已经复制，所以新增消息不会出现，长度断言仍通过。
B仍通过：CLI构造消息并调用记录器，但没有在之后修改消息，因而这次没有触发浅复制问题。
这说明“集成测试通过”不等于“记录器的所有行为都正确”，聚焦测试A仍然必要。

</details>

## 5. 恢复并检查CLI测试的替换边界（10分钟）

恢复第2步的正确循环，保留断言，执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_d004.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前应分别为2项、21项通过。以后增加测试后，完整数量可能变化。

阅读测试B的with块，对照这张表：

| 替换项 | 为什么需要 |
| --- | --- |
| 环境变量 + 临时ENV_PATH | 没有真实Key，也不会从本地.env加载它 |
| input | 测试自动提供同一句玩家原话 |
| FakeModelAdapter | CLI实际拿到我们创建的记录器 |
| RealModelAdapter | 如果分支走错，立刻失败 |
| RUN_PATH | 写临时文件，不污染以前的验收记录 |
| redirect_stdout | 把终端输出存起来，用断言检查固定回复 |

build_view、build_prompt、cli.main都是真实函数；如果把它们也替换掉，就无法验证实际的信息过滤流程。
为什么 patch cli.FakeModelAdapter？因为 main 在 cli 模块中查找这个已经导入的名字；替换需要发生在实际使用的位置。

你可以在with块结束后临时加入下面一行观察记录（看完删去打印即可）：

```python
print(json.dumps(model.calls, ensure_ascii=False, indent=2))
```

确认外层是调用记录，下一层有system和user；玩家原话在user里，只有林砚可见事实在system里。
记录器不需要理解秘密，assertNotIn直接检查完整messages是否含有被禁止传入的文本。

## 6. 用三个问题对照Pydantic AI（5分钟）

只读下面三项，不安装该框架，不迁移本项目。

| 要看什么 | 官方做法 | 本课对应 |
| --- | --- | --- |
| 固定回复在哪里设置 | TestModel的custom_output_text | RecordingFakeModel(reply=...) |
| 如何换掉真实模型 | Agent.override(model=...) | patch cli.FakeModelAdapter |
| 如何检查消息 | capture_run_messages | model.calls及完整消息断言 |

[官方测试指南](https://pydantic.dev/docs/ai/guides/testing/)说明了替换模型及捕获消息的方式；[TestModel源码](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/models/test.py)中搜索 custom_output_text、_get_output，可以追踪配置的文本如何成为输出。这是思路对照，calls不是照抄框架里的同名属性。

## 本人实践记录

```text
运行前预测：
错误版本实际失败的测试和断言：
为什么追加消息没污染记录，修改content却污染了：
为什么测试B仍然通过：
恢复后聚焦测试 / 完整测试结果：
哪些函数是真实执行，哪些边界被替换：
实际耗时 / 卡点：
```

本课做到这里即可。已保存的AI实验见 [d004_results.md](d004_results.md)；它可以核对你的结果，但不代替你自己的预测和解释。
