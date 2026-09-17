# A07 SQL 小表练习（AI 参考）

使用 Python 标准库 sqlite3 的 `:memory:`，不连接 WorldEngine，不迁移业务存档。代码：[练习](../exercises/a07_sql.py)；原预测位置：[a07_prediction.md](a07_prediction.md)。运行输出由 a07_verify 保存到 a07_sql_output.txt；个人手算尚未提交。

四行数据是 L01/v1/public/NULL、L02/v1/public/空字符串、L07/v1/lin_yan/旧便笺、L01/v2/public/修订。

- SELECT 先表达权限条件，再给出确定的 source_id/version 排序和 LIMIT，结果是 L01/v1 与 L01/v2。SQL 语法书写次序不是物理执行计划。[SQLite SELECT](https://www.sqlite.org/lang_select.html)
- `IS NULL` 匹配缺失 note 的 L01/v1；`= NULL` 的条件不能为真，不返回行。空字符串是 L02/v1，既不是 NULL，也不表示缺失 ACL 可以公开。[SQLite 表达式](https://www.sqlite.org/lang_expr.html)
- public 组 COUNT(*)=3、COUNT(note)=2；lin_yan 组分别为 1、1。后者只数非 NULL，因此分母不同。[SQLite 聚合函数](https://www.sqlite.org/lang_aggfunc.html)
- `(source_id, version)` 非空复合唯一约束允许 L01/v1 与 L01/v2，拒绝同 ID 同版本重复；`(visibility, source_id)` 组合索引用 EXPLAIN QUERY PLAN 观察，不据此宣称小表获得了性能收益。[SQLite 索引](https://www.sqlite.org/lang_createindex.html)
- BEGIN 后成功插两行，再触发重复键错误，此时还有 6 行；应用明确 ROLLBACK 后恢复 4 行。不能假定任意单句错误会回滚整个事务。[SQLite 事务错误处理](https://www.sqlite.org/lang_transaction.html)

独立练习：先保存自己的五项预测，运行，再解释结果与预测的差异；本轮 AI 运行不计个人成绩。A10 再把事务与索引引入业务持久化。
