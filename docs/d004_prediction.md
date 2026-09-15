# D004 浅复制错误：运行前预测

记录时间：2026-09-15T15:49:15+08:00。
记录者：AI。此文件在修改错误实现和运行错误版本之前写入，不代表用户本人预测。

初始正确版本：两个聚焦测试通过，见 d004_initial_tests.txt。

## 预测

把保存逻辑改为 `self.calls.append(messages.copy())` 后：

- `test_records_independent_snapshot` 应失败在 `model.calls[0][0]["content"] == "你好"`。
- 原因：外层列表是新的，但第一个字典仍和 messages[0] 是同一个。改写 content 后，记录里也变成“后来修改的内容”。
- 记录长度仍为1，后追加的消息不会进入新列表，所以长度断言应通过。
- `test_cli_uses_recording_fake_and_filtered_messages` 预计仍通过，因为本次 CLI 不会在调用后修改原消息。
- 应得到一次断言失败，不应是语法或导入错误。之后恢复原实现，保留全部断言，再跑聚焦测试和完整回归。
