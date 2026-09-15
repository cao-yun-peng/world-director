# 运行前预测（AI）

将记录器改为只保存 messages.copy()，test_t02_snapshot_and_old_session_are_independent 应在“原文”快照断言失败：内部字典共享，原输入改成“被修改”后旧记录也变化。外层追加不会污染长度。恢复实现后新增及完整测试应重新通过。
