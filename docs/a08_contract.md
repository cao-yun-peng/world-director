# A08 实施契约

- 任务：A08-IMPLEMENT-20260917；revision：2；风险 L0，本地虚构教学。
- 基线：eca50dd4a0e1e026282af7a169d03891e016aff8；开工工作区干净。
- 唯一进度事实源：现有 docs/aXX_results.md；A08 使用 docs/a08_results.md。Project-to-Act 发现为 unconfigured，按用户计划不初始化平行账本；生命周期 revision 不适用。
- 范围：D050—D056 的离线工程、显式真实适配入口、复现实验及待验收记录。允许修改 app/、tests/、scripts/、data/eval/、exercises/a08_variant.py、docs/a08*、README.md、agent.md。
- 不变量：权限前置且不扩大；检索不改变世界；Fake/Real 分开；外部请求均受预算；重放与续玩不重复副作用。
- 检索冻结：a07-lore-v1，a08-dev-v1，candidate_k=5/路，RRF 常数 60，top_k=3；原默认 off 不变，重排默认 off。
- 存档：可信本机、轮边界、版本化 JSON、专用槽位、原子替换；保存完整世界/回执/角色历史/剧情/预算，拒绝不兼容文件，不支持迁移与并发写。
- 验证：专项与全量 unittest；四路离线检索对照；20 轮 Fake 场景与跨进程续玩；旧演示回归写入新目录，不覆盖旧证据。
- Gate：离线、真实、本人掌握分列。真实供应商未在本任务显式开启，不运行付费服务，不将 Fake 结果当质量证明。本人预测/独立解释待本人填写。
- 任务状态：离线实施 complete；真实与学习 Gate pending；执行者 Codex。完整 A08 仍部分完成。
- A08-FINAL：最终冻结专项 33 项、全量 311 项及七个命令全部 exit 0；具体命令、哈希及有效期见 a08_evidence_manifest.json 指向的清单。
- 2026-09-17 checkpoint：检测到并行 A07 真实收尾；保留其业务修复和独立证据，最终回归包含 2 项新增 A07 测试。其短暂诊断字段兼容失败已由本轮修复，不覆盖旧失败记录。
- 交接：当前代码/数据版本固定于最终 source.zip；后续改动须更新证据。学习者负责原预测、真实费用/额度选择与独立验收；实施者按同一 A08 计划继续真实对照和自由试玩，不跳到 A09。
