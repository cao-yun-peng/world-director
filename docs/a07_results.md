# A07 开发与验收进度

2026-09-17，revision 1，任务 A07-IMPLEMENT-20260917，风险 L0。**D043—D049 离线工程交付完成；真实检索、真实生成与学习者独立验收待完成，不能宣布整课通过。**

实际基线为 `7e9f1bff2e1e5729334032b7e2ebaf8ec6dc81c7`（A06），开工工作区干净，本轮尚未创建 Git 提交。Project-to-Act 只读发现为 unconfigured；沿用 docs/aXX_results.md，不初始化第二套账本，正式生命周期 revision 不适用。契约见 [a07_contract](a07_contract.md)，用户原计划保留在 [a07_plan](a07_plan.md)。

## 完成内容

| 课程 | 实际交付与边界 |
|---|---|
| D043 | data/lore/handover.json 的 12 份虚构设定；UTF-8、明确主体集合、版本、场景、内容哈希；缺 ACL、冲突重复、错版、空文拒绝；只读不可变快照 |
| D044 | app/lore.py 按文档/权限/原段切块；每块最多 400 Unicode code point；当前种子 12 块；0 起始段号，LF 归一后的左闭右开偏移；无重叠、无摘要、保留否定词 |
| D045 | app/retrieval.py：NFKC/casefold、中文二元组/英文单词，交集数除以词集合大小乘积的平方根；先 ACL 后评分，正分才返回，同分按 chunk_id；三条固定查询定位 L02/L04/L12 |
| D046 | app/embeddings.py：确定性哈希 Fake + OpenAI 兼容真实适配；缓存按 mode/provider/model/version/dimensions/preprocessing/prefix/purpose/text_hash；批内去重、同缓存串行并发去重、失败不缓存、返回拷贝、关闭客户端 |
| D047 | 显式开局前构建内存不可变索引；全部向量校验后发布；余弦、数量/维度/有限值/非零范数与空间检查；空授权集合不编码查询；不引入数据库服务或混合排名 |
| D048 | scene 显式 lore 模式，原生 search_lore(query, top_k)；程序绑定 actor/player/场景/快照；新增 lore_refs，不改变 A06 event source_refs；检索、实际交付、模型采用分别记录 |
| D049 | 两后端各运行有据/无据/无权三条路径，共 6 例；故障、镜像、引用、取消、额度、重放、部分完成和旧结局回归 |
| SQL | 独立 sqlite3 内存小表：SELECT、NULL、GROUP BY、复合唯一及组合索引、显式回滚；不迁移业务 |
| 源码 | 固定 Pydantic AI 提交的 Deps/RunContext 与 retrieve/insert_doc_section 两处对照；仅 AI 静态阅读参考，未安装框架 |
| 独立变式 | 提供 L12 撤去玩家权限、升级版本与新局练习；AI 参考运行验证玩家请求不含 L12、角色自身仍可查；不计学习者成绩 |

设定版本 `a07-lore-v1`；运行协议 `a07-lore-v1`；切块规则 `paragraph-codepoint-400-v1`；分词规则 `nfkc-casefold-cjk-bigram-english-word-v1`；Fake 模型 `fake-bigram-v1`、256 维。向量模式默认 Fake 阈值 0.25，只是教学值；真实阈值必须显式配置，尚未校准。供应商版本标记 `provider-unpinned`，不保证模型别名长期不变。

## 权限、引用和预算

唯一授权函数按“角色可读 ∩ 输出接收者可读 ∩ 当前场景/版本”筛选。标题、正文、来源 ID、命中数都只来自授权集合；无命中与无权均返回 NO_USABLE_EVIDENCE，不返回隐藏候选计数。改变隐藏标题和正文的镜像测试中，公开检索、实际模型请求、普通输出相同。没有把全库哈希加入角色请求。

本日生成接收者固定 player。lore 模式不提供 whisper，避免把面向玩家的检索内容转发到未授权角色；旧 memory/scene 模式的 whisper 维持原行为。角色私有检索只用于无生成的受控测试。私人设定不进入其他角色历史、导演或结尾上下文。

`lore_refs` 引用 chunk_id；程序保存 source_id/version、段号、起止位置、正文哈希。检索 trace 含排名和分数，delivery trace 记录实际请求保留的片段，decision_summary 记录模型声称采用的片段。预算不足时整片删除，保持完整 assistant/tool 协议块；丢弃、未返回、错版、别的角色片段不能被接纳为本轮来源。必要协议本身超限则 CONTEXT_BUDGET_EXCEEDED；删到零片段则工具状态 CONTEXT_LIMIT。

工具本身不提交 DiscoveryEvent、TransferEvent 或记忆广播。正常 end_turn 仍可记录“角色说过”的 StatementEvent；即使模型引用 L04 声称已交接，也不会改变归属。该反例故意被格式检查接纳、账本保持林砚所有，说明**引用正确不等于语义正确**。后续 give 仍经过原裁定器。

默认 4 步/角色、2 工具/批、场景共用 30 秒与 8 次外部尝试，其中在线 Embedding 最多 2 次；重试计数，缓存命中不计外部请求。为了兼容旧调用方，`model_requests` 表示聊天+在线 Embedding 总数，并新增 chat_requests/embedding_requests 分项。索引最多 24 条文本、8 次外部尝试/30 秒，显式开局前构建；CLI 进程总额也扣除构建尝试，/new 不清零。默认每次 messages+tools 不超过 8000 Unicode 字符，字符不是 token。

RETRIEVAL_UNAVAILABLE 与无依据分开；请求上限和 deadline 继续中止当前循环。第二角色失败保留第一角色已提交事实；场景重放在任何检索/编码/规划前返回原记录，新增调用为零。

## 实测证据

使用仓库 `.venv/Scripts/python.exe -X utf8`，离线子进程禁用 dotenv。最终完整命令、UTC 时间、退出码和原始字节哈希见 [a07_evidence_manifest.json](a07_evidence_manifest.json)。一键复跑：`python -X utf8 -m scripts.a07_verify`；旧日志自动另存，A05/A06 演示写入 A07 新文件。

| 证据 ID | 实际结果 | 证据 |
|---|---|---|
| A07-BASE | 修改前全量 228 项通过，exit 0 | [基线](a07_baseline_tests.txt) |
| A07-T | A07 专项 48 项通过，exit 0 | [专项输出](a07_tests.txt) |
| A07-REG | 全量 276 项通过，包含上述 48 项，exit 0 | [全量输出](a07_all_tests.txt) |
| A07-DEMO | keyword/vector_fake 各三路径，6 例及重放断言通过 | [概要](a07_demo_output.txt)、[请求/来源/世界证据](a07_retrieval_probe.json) |
| A07-A05 | 保留/转述隔离、上下文预算、共享 history 故障检测通过 | [输出](a07_a05_output.txt)、[证据](a07_a05_regression.json) |
| A07-A06 | 交接/暂缓两结局与玩家改选通过 | [输出](a07_a06_output.txt)、[证据](a07_a06_regression.json) |
| A07-SQL | IS NULL 一行、= NULL 零行；COUNT 分母不同；失败后 6 行，显式回滚恢复 4 行 | [SQL 输出](a07_sql_output.txt) |
| A07-VAR | 新版权限变化后玩家请求无 L12，林砚受控私有检索仍可读 | [变式输出](a07_visibility_output.txt) |
| A07-A06-VAR | 同地给物成功，离场变式 NOT_COLOCATED 且拒绝不变更世界 | [旧变式输出](a07_a06_variants.txt) |
| A07-WAIT | 原 wait 小步演示通过 | [输出](a07_wait_output.txt) |
| A07-DIFF | git diff --check 通过 | 证据清单 commands |

过程保留：首批 21 项通过、第二批 38 项通过、首次全量 266 项通过；文件名带 first_run/second_run。后续增加边界覆盖得到最终 48/276 项。运行记录中的 injected real 适配器只用于扣费计数故障测试，未访问供应商，不是 Real 质量证据。

## 操作入口

```powershell
# 完全离线，不读取密钥：
.\.venv\Scripts\python.exe -X utf8 -m scripts.a07_demo
.\.venv\Scripts\python.exe -X utf8 -m scripts.a07_verify
.\.venv\Scripts\python.exe -X utf8 -m exercises.a07_sql
.\.venv\Scripts\python.exe -X utf8 -m exercises.a07_visibility

# 使用已配置的真实聊天模型，设定走本地关键词（本轮未运行）：
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine scene --lore-mode keyword --max-model-requests 24
# 显式 Fake 向量；聊天仍用既有真实聊天适配器（本轮未运行）：
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine scene --lore-mode vector_fake --build-lore-index --max-model-requests 24
```

真实 Embedding 需独立配置 `EMBEDDING_API_KEY`、`EMBEDDING_MODEL`、`EMBEDDING_BASE_URL`（HTTPS）、`EMBEDDING_DIMENSIONS`。本轮只检查配置项是否存在，没有输出密钥；发现仅有 LLM_API_KEY/LLM_MODEL，不能推断 Embedding 型号与维度。

配置完成后，可显式运行 `python -m scripts.a07_live_probe --real --allow-lore-upload --min-score 0.5 --generate`。这会上传仓库虚构种子与固定查询，并运行真实三条回答路径，整个探针最多 24 次外部尝试；0.5 只是待校准的初始值。结果在本地忽略的 runs/a07_real_probe.json 与 runs/a07_real_io/，人工逐条审查后才判断质量。交互式真实向量另用 `--lore-mode vector_real --build-lore-index --allow-lore-upload --lore-min-score 0.5`；未配置直接失败，只有显式 `--lore-fallback-keyword` 才回到关键词且保留原因。

## Gate 与交接

| 层次 | 结论 | 关闭条件 |
|---|---|---|
| 离线工程 | 已通过本轮验收矩阵，无已知未处置工程阻塞 | 实现/数据改变后复跑 |
| 真实检索 | 未运行；缺少独立 Embedding 配置 | 用户提供模型、地址、维度并本机配置密钥；显式探针运行，审查同义问法与无依据样例 |
| 真实回答 | 未运行；独立显式步骤 | 用实际模型完成有据/无据/无权，检查请求隔离、支持度、忠实性与措辞 |
| 独立学习 | 待验收；未收到原预测 | 学习者保存预测，亲自改 L12 变式、运行并解释；独立 SQL/源码复述 |
| 前序课程 | A05/A06 离线回归通过；旧真实与学习欠项仍保留 | 不因推进 A07 自动判前序整课通过 |

AI 帮助范围：本轮实现、测试、文档、源码阅读和参考练习运行均由 AI 完成；不记作个人独立成果，不给整课总分。原答案占位保留在 [a07_prediction](a07_prediction.md)。源码和 SQL 参考分别见 [source_notes](a07_source_notes.md)、[sql_notes](a07_sql_notes.md)。

工时：未连续记录会话总工时；日志有各验证命令时间，不把工具耗时称为学习者投入或两小时完成证明。回退可使用默认 `--lore-mode off`，或旧 memory/loop 入口，无数据迁移。未安装新依赖、未启动数据库、未发布或提交 Git。A08 的 RRF/混合排序/重排、20 条开发集和 20 轮互动未提前实施。
