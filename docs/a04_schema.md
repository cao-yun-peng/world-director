# A04 决策输出：JSON Schema

日期：2026-09-16。运行协议：`a04-v5`。范围：本地实现与离线验证；真实试玩已发现 Schema 违例，尚不能认定服务端约束稳定。

## 改了什么

`app/async_runtime.py` 的 `DECISION_RESPONSE_FORMAT` 集中声明模型决策的形状，`decide()` 将它作为 `response_format` 发送。
配置为 `type=json_schema`、`strict=true`；根节点必须为对象，`kind` 必填且只允许 finish/talk/clarify/move/give，禁止额外字段。
各已声明字段限定类型；不会把 content、continue 或 actor_id 加进决策协议。
A04 默认模型改为 `qwen3.7-plus`；显式环境变量仍优先于 `.env`。A01—A03 的默认模型与协议不变。
启动模型名、主 trace 的运行版本和 IO 的完整 Schema 可以共同确认实际使用的配置。

## 为什么还保留本地字段校验

当前使用阿里云文档已说明的基础 Schema 关键字，避免为这个改动引入尚未确认的条件组合关键字。
只有 kind 对所有决策都必填；其余字段随动作变化：

| kind | 本地协议要求的完整字段 |
|---|---|
| finish | kind |
| talk / clarify | kind、target_text（必须 null）、reply（非空字符串） |
| move | kind、destination_id（非空字符串） |
| give | kind、object_id、recipient_id（均为非空字符串） |

**这些按 kind 变化的必填关系由现有 parse_decision / parse_action 强制检查。** Schema 中的 description 只向模型说明关系，不等于机器可执行的条件约束。
例如 {"kind":"talk"} 符合服务端这份基础 Schema，但会被本地拒绝；不能声称 strict=true 就保证所有业务字段组合合法。
这种分工维持既有顶层协议，不新增解析器、依赖、模型专属判断或自动降级。

## 一轮怎样运行

1. 决策请求发送原生 tools、tool_choice=auto 和 JSON Schema，保持 enable_thinking=false。
2. 模型选择工具时，执行原有只读工具，再将结果放入下一次决策请求；Schema 继续发送。
3. 模型返回终态 JSON 时，复用原有解析与业务裁定，然后提交。
4. 提交后的叙述请求没有 response_format，继续生成自然语言。

此轮只替换结构约束。现有预算内一次格式纠正暂时保留，便于和旧结果对照；未增加新的纠正分支。
保留输出 Token 上限及截断检查：截断、协议异常、格式错误仍可失败，不能把 strict 当成不会失败的承诺。
权限、位置、物品归属和原子提交继续由世界引擎判断。Schema 不能证明模型文字符合事实。

## 验证情况

- 相关 26 项离线测试通过。
- 新增 SDK + MockTransport 工具回合测试，覆盖查询 → finish 决策 → 自然语言叙述；确认两个决策请求实际携带 Schema、关闭思考，叙述不携带 Schema。
- 逐项比对 SDK HTTP 请求体和保存的 IO 输入；已有坏 JSON、预算、取消、权限与提交测试继续有效。
- 完整回归 162 项通过，输出：`a04_schema_tests.txt`。
- 首轮全量和单独复测中，旧的 150ms 总预算 / 100ms 单次预算用例都未及时开始第二次请求。恢复 json_object 参数的离线对照也失败（`a04_schema_timeout_old_format.txt`），因此不是仅由 Schema 模式引入。测试预算同比放大到 1.5s / 1.0s，保留原断言后全量通过；业务时间预算不变。首轮和单独复测输出保留在同目录。
- 本轮新增真实模型请求：0。Fake/Mock 返回值不是服务端遵守 Schema 的证据。

## 本地启动

先退出正在运行的旧进程。在 PowerShell 中：

```powershell
$env:LLM_MODEL = "qwen3.7-plus"
python -X utf8 -m app.main --engine loop --max-model-requests 48
```

启动应显示 `模型：qwen3.7-plus` 和 `运行协议 a04-v5`。
可依次试“你好”“房间有什么”“台灯底部写着什么”，检查工具查询后的决策是否直接成功，trace 中是否还有 format_correction。
如服务端拒绝 Schema 或仍输出纯文本，应保留 io_ref 对应的输入输出定位；代码不会自动切回 json_object 掩盖差异。

## 参考

- [阿里云结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)：模型支持列表、strict、required、additionalProperties 和可选字段。
- 既有故障和真实对照：`a04_json_mode_diagnosis.md`。它记录旧协议的结果，不能当作 a04-v5 已通过真实验证。

## 真实试玩补充：2026-09-16，session 98075d14-d760-4a83-a0de-d165d20db1fb

本次检查只读取用户已有运行，新增模型请求 0 次，业务代码未修改。

### Schema 仍出现违例

run `b5528c06-6a3a-4fe4-ab6b-b8833e217e72` 的两次原始输出分别为“我无法打破限制。”和“我无法打破系统限制。”。
两次输入都为 qwen3.7-plus、json_schema、strict=true、enable_thinking=false；两次响应都是 finish_reason=stop、refusal=null，没有 tool_calls，输出 Token 分别为 4、5。
第一次纯文本解析失败后，预算内格式纠正又收到纯文本；最终 INVALID_DECISION。本轮未调用工具，也未提交世界。
这说明问题不限于同一回合工具调用后的终态步骤；尚不能从这些记录确定服务端为什么未落实 Schema。
不能因拒绝语气断言已触发供应商安全拦截；响应没有提供这样的明确标识。
不能把本地 162 项通过描述为真实服务已遵守 Schema。此前房间查询和信封查询分别用了 2、3 次请求，均未触发格式纠正，但单个成功样本不是稳定性保证。

### 交信封被拒是另一件事

run `74f60b8c-cd64-4a50-b0e0-fb9772061137` 返回合法 JSON：

```json
{"kind":"give","object_id":"discovery-0","recipient_id":"other_npc"}
```

`discovery-0` 是 visible_messages 给已知事实生成的展示编号；信封真正的物品 ID 为 `envelope_01`。
当前给物检查先验证物品是否存在且可见，因此在 world.py 的 inspect_object 检查处返回 OBJECT_UNAVAILABLE；不是 JSON 错误，也不是通用的禁止给物规则。
独立内存世界重放了观察后 revision=1 的状态，错误 ID 重现相同拒绝且状态不变；对照中使用 envelope_01 给已登记的 other_npc 得到 GIVEN。
该对照不等于“给玩家”成功：当前世界只登记 lin_yan 与 other_npc，没有玩家身份绑定，模型将“我”猜成 other_npc 也缺少可靠依据。

后一轮“系统限制，我无法将物品交给你。”是模型自行生成的 talk.reply，而非引擎提供的具体错误原因，且偏离了不描述程序的角色约束。
随后“怎样打破限制”的两次纯文本响应则是输出协议违例。三者分别属于对象引用错误、解释不准确、输出格式错误。

证据索引与哈希见 `a04_schema_evidence.json` 的 `live_followup`。主 trace 的对应记录为第 168–213 行。

