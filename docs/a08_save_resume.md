# A08 最小存档与续玩

场景入口新增 /save 槽位、/load 槽位，启动时使用 --load-slot。
槽位只能包含字母、数字、下划线或短横线，最长 48；拒绝路径和 Windows 保留名。
实际文件保存在被忽略的 saves/scenes/，不保存密钥、客户端、锁、异步任务或全量调试 prompt。

```powershell
# 已明确开启真实聊天；本轮文档命令未执行
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine scene --scene-profile a08 --enable-real --lore-mode keyword
# 在终端完整轮边界输入：
# /save lesson08
# /exit

# 另启进程，保持相同模型、检索与每轮配置：
.\.venv\Scripts\python.exe -X utf8 -m app.main --engine scene --scene-profile a08 --enable-real --lore-mode keyword --load-slot lesson08
# /retry 重放保存前上一轮，随后输入新文字续玩
```

离线验证用 scripts.a08_demo 自动启动两个独立 Python 进程；测试不加载 .env。
最终验证还覆盖：损坏文件、旧/未知 schema、设定配置不匹配、事件与状态不一致、
非法角色/孤立历史/私有引用、假场景投影、原子替换失败保留旧档、结束档、部分完成档、
跨角色历史隔离、预算/轮数恢复、同 ID 参数冲突以及加载旧槽位不补本进程预算。

保存先校验完整快照，再写同目录临时文件、flush/fsync、原子替换；失败清理临时文件。
加载先校验版本、校验和、固定设定 manifest、检索/向量空间/重排配置和聊天配置；
依据已有事件调用纯裁定器验证状态，随后一次性恢复新对象。不调用模型还原，不重复 start/机会事件。
角色摘录缓存可按授权历史重新派生；导演计划与有效回执保留。

每故事请求分项、已用轮数、上限与每轮 RunLimits 均保留；跨进程恢复保存时的批次余量。
同进程 /load 合并时使用更严格上限和更高已用量，/new 沿用同一批次预算。
向量索引不写入存档，跨进程显式 --build-lore-index 重建；真实上传仍需 --allow-lore-upload，
构建请求也从恢复后的批次额度扣除。不兼容存档在外部请求前拒绝。

边界：可信本机、轮与轮之间、单进程写入；最多 8 MB。不支持模型生成中恢复、自动版本迁移、
多进程同时写、云同步、用户认证或分支合并。保存文件含虚构角色私密资料，应留在本地。
SHA-256 用于检测损坏，不是防篡改签名；跨进程复制/回滚旧档不构成生产级全局计费控制。
