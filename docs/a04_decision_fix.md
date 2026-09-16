# A04：真实问答 INVALID_DECISION 的诊断与格式修复

日期：2026-09-16。任务 A04-DECISION-FIX，revision 1，离线修复验证完成。

## 实际证据

用户报告：问候成功后，“你是谁”连续三次得到 INVALID_DECISION，其中一次是 /retry。
核对本地 runs/a04.jsonl 中对应 run：

- bbf33cd2-af66-47a4-875b-52dd2da04ba5
- a8f71711-5695-40fb-801a-46a129f0cf31
- 4ea80544-7f23-4909-a01b-c8b4be85f019

每个 run 都实际发送了一次模型请求，model.status=ok，随后终止为 INVALID_DECISION，
committed_revision=null，没有 commit 记录。最后两个 run 的 turn_id 相同，说明 /retry 的重发编号正常。
三次服务端 usage 均报告 completion_tokens=9。旧日志没有输出正文和字段诊断，不能据此认定具体返回了哪句话或缺哪个字段。

结论：请求已返回，内容未通过本地决策协议；故障位于协议解析，尚未结算世界。
本次只读取模型配置名称 qwen-plus 和上述脱敏轨迹，没有发送新的真实模型请求。

## 代码中的薄弱点

决策接口要求 JSON，但旧版仅用提示词约束；先前 assistant 历史却是向玩家展示的普通对白。
这会给模型混杂的格式示例。它是否直接返回了对白，仍属推断，旧日志无法恢复原文。
原解析器把非法 JSON、缺字段、未知 kind 等全部压成 INVALID_DECISION，CLI 又统一建议 /retry，无法解释重复失败。

## 修改

1. 决策请求显式设置 response_format={"type":"json_object"}；原生 tools/tool_choice=auto 保留。叙述阶段仍输出普通文字。
2. 提示词明确：历史对白不是本次接口格式示例，角色语言放在 reply 中；增加身份问答的完整 JSON 示例，null 字段必须填写。
3. 空输出、非法 JSON、已知决策缺字段且没有额外字段时，最多提供一次脱敏格式反馈，进入新的逻辑决策。
4. 纠正使用下一个 step，实际发送照常计数，共用原 deadline。纠正失败终止；额外身份字段、未知类型与非法字段值直接拒绝，不删除字段、不从普通文字推断行动。
5. trace 增加 decision/error，记录白名单结构诊断：shape、已知 kind、缺失字段、额外字段数、已知字段类型。不保存正文、字段值或未知字段名称。
6. CLI 解释实际格式错误，不再对 INVALID_DECISION 统一提示可用 /retry 重试。

JSON Object 约束只保证 JSON 格式，不保证业务字段一定正确，所以保留本地严格校验。
依据：[千问官方结构化输出说明](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。

## 验证

- 新增 9 项专项测试：模拟先问候再问身份、普通文字纠正、遗漏 null、纠正耗尽、额外字段拒绝、步数/请求/时间预算、候选观察回滚、原生工具路径和 CLI 错误解释。
- 真实 SDK + MockTransport 检查请求体携带 response_format，并保留原生工具调用 ID。
- 9 项新增专项通过；全部 146 项测试通过；git diff --check 退出 0。
- 输出：a04_decision_fix_tests.txt、a04_decision_fix_all_tests.txt；源码/证据哈希：a04_decision_fix_evidence.json。

这些验证不等于 qwen-plus 真实服务已重新验收。当前旧进程需退出后重新启动才会加载修改；重启会新建内存世界。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine loop --max-model-requests 48
```

真实连续试玩和学习者独立变式仍待验收，本次不变更阶段 A 的总通过状态。
