# A03 真实交互 INVALID_ACTION 修复

## 已确认的事实

用户输入“看看当前场景”后退出。原轨迹显示 a03-v1、一次模型请求、
finish_reason=stop、intent=null、tools=[]、termination_reason=INVALID_ACTION。
说明模型文本返回后未通过行动解析，尚未执行工具或结算世界。
旧轨迹没有模型正文或字段诊断，无法确定这一次具体缺了哪项字段。

## 修复

- 提示词更新为 a03-v2，加入完整 JSON 示例，明确 null 字段不能省略、
  get_visible_scene 是工具名而不是行动 kind。
- 已知行动出现缺失字段且没有额外字段时，最多请求一次重新生成。
  原始错误响应不回填到模型请求，身份字段不能通过纠正被默默丢弃。
- 每轮最多三次模型请求，纠正占用现有预算。
  如观察结算后没有剩余叙述预算，展示确定性场景回执。
- action_errors 仅保存固定错误信息、已知字段名、类型、缺失项和额外字段数量，
  不记录字段值、模型正文或未知字段名。
- world 模式结算前发生 AgentTurnError 后保留当前世界和历史，继续等待输入；
  失败请求也扣预算，可以 /retry 重试。A02 原行为不变。
- 日志识别所有 a03- 版本，继续写入 runs/a03.jsonl。

## 验证

命令：python -m unittest discover -s tests -v
结果：Ran 100 tests in 3.232s / OK，退出码 0。
新增 5 项针对遗漏字段、纠正上限、身份拒绝、请求预算和 CLI 保留世界的测试。
完整输出见 [a03_protocol_fix_tests.txt](a03_protocol_fix_tests.txt)。
git diff --check 通过。

真实 qwen-plus 复现请求被自动审批拒绝，未发起请求。
原因：自动审批要求用户明确授权向外部服务发送本次角色/场景诊断上下文。
因此目前能确认本地程序与故障处理修复通过，不能声称真实模型已经验证成功。
