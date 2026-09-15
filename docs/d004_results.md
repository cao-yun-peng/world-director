# D004：记录型假模型与浅复制错误实验

## 结果

| 阶段 | 聚焦测试结果 | 证据 |
| --- | --- | --- |
| 正确实现首次运行 | 2/2通过 | [原始输出](d004_initial_tests.txt) |
| 运行前预测 | A失败、B通过；预期字典共享污染旧记录 | [先写下的预测](d004_prediction.md) |
| 临时使用 messages.copy() | A按预期发生1次断言失败；B通过 | [失败输出](d004_shallow_copy_failure.txt) |
| 恢复正确实现 | 2/2通过 | [恢复后输出](d004_restored_tests.txt) |
| 完整回归 | 21/21通过 | [完整输出](d004_tests.txt) |

实际失败：

```text
self.assertEqual(model.calls[0][0]["content"], "你好")
AssertionError: '后来修改的内容' != '你好'
FAILED (failures=1)
```

断言发现的是输入快照被后续修改污染，不是语法或依赖问题。错误版本复制了外层列表，所以追加消息不会污染快照；列表里的字典仍共享，改写 content 会污染快照。
恢复时将测试文件逐字节还原为正确版本，未删除或放宽断言。

## 实现范围

- 只新增 tests/test_d004.py：一个 RecordingFakeModel、两个聚焦测试，没有新增依赖或框架。
- generate 使用直白循环创建新列表并复制每个消息字典，再返回 self.reply；当前 role/content 都是字符串。
- 测试A检查原输入未被记录器修改、固定回复、调用次数、原文本和追加消息的隔离。
- 测试B真实运行 cli.main、角色卡、build_view、build_prompt 和日志流程。只替换输入、环境、路径及模型边界。
- 测试B将 cli.FakeModelAdapter 替换为记录器；cli.RealModelAdapter 一旦构造就抛 AssertionError。临时 .env 不存在，环境变量清空，日志写临时目录。
- 完整请求中断言隐藏事实的 ID、正文、校验标记及后台备注均不存在；有权事实必须存在。
- 固定回复同时在终端和 JSONL 验证，日志必须 mode=fake。CLI仍属于D003，所以临时日志保留 day=D003、prompt_version=d003-v1；D004只增加测试能力。
- 未调用真实模型，未读取真实 .env；原 runs/*.jsonl 的文件集合及 SHA-256 均保持不变。

## 复现命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_d004.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

保存输出时加了 -X utf8，仅控制中文编码。环境：Python 3.12.10、openai 2.50.0、python-dotenv 1.2.2。

## 证据能说明什么

本轮证明了两个测试能检查当前CLI输入与日志，并能识别指定的浅复制错误。不代表涵盖所有输入，也不能证明真实模型的人设表现或事实准确性。
测试B在错误版本仍通过是预期结果：它没有在调用结束后改写原消息。测试A专门承担这个检查，二者互补。

## 实际执行说明

- 基线 commit：a6892c0；本次未创建新 commit。
- 首次错误注入脚本因 Windows CRLF 与匹配串换行不同，在修改文件前的检查处停止，未运行错误测试。规范化匹配换行后才执行上述实验；该脚本错误没有当作预期测试失败。
- AI范围：实现、预测、故意错误实验、恢复、测试、学习资料。结果是AI执行证据，不代替本人动手证据。
- 本人预测、失败解释及实际学习耗时可填写在 [实践指南](d004_practice.md) 末尾。
