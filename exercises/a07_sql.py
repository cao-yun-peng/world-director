"""独立于 WorldEngine 的 SQL 小表实验；AI 参考运行不计学习者独立成绩。"""

import json
import sqlite3

# 请先把自己的 SELECT / NULL / COUNT / 回滚预测写入 docs/a07_prediction.md。
LEARNER_PREDICTION = ''


def run_experiment():
    connection = sqlite3.connect(':memory:', isolation_level=None)
    try:
        connection.execute('''CREATE TABLE lore (
            source_id TEXT NOT NULL, version TEXT NOT NULL, visibility TEXT NOT NULL,
            note TEXT, UNIQUE(source_id, version))''')
        connection.execute('CREATE INDEX lore_visibility_source ON lore(visibility, source_id)')
        connection.executemany('INSERT INTO lore VALUES (?, ?, ?, ?)', [
            ('L01', 'v1', 'public', None), ('L02', 'v1', 'public', ''),
            ('L07', 'v1', 'lin_yan', '旧便笺'), ('L01', 'v2', 'public', '修订')])
        queries = {
            'select': "SELECT source_id, version FROM lore WHERE visibility = 'public' ORDER BY source_id, version LIMIT 2",
            'is_null': 'SELECT source_id, version FROM lore WHERE note IS NULL ORDER BY source_id, version',
            'equals_null': 'SELECT source_id, version FROM lore WHERE note = NULL ORDER BY source_id, version',
            'empty_string': "SELECT source_id, version FROM lore WHERE note = '' ORDER BY source_id, version",
            'group': 'SELECT visibility, COUNT(*), COUNT(note) FROM lore GROUP BY visibility ORDER BY visibility',
            'query_plan': "EXPLAIN QUERY PLAN SELECT source_id FROM lore WHERE visibility = 'public' ORDER BY source_id",
        }
        output = {name: {'sql': sql, 'rows': connection.execute(sql).fetchall()} for name, sql in queries.items()}
        before = connection.execute('SELECT * FROM lore ORDER BY source_id, version').fetchall()
        connection.execute('BEGIN')
        connection.execute("INSERT INTO lore VALUES ('X01','v1','public','一')")
        connection.execute("INSERT INTO lore VALUES ('X02','v1','public','二')")
        try:
            connection.execute("INSERT INTO lore VALUES ('L01','v1','public','重复')")
        except sqlite3.IntegrityError:
            output['before_explicit_rollback_count'] = connection.execute('SELECT COUNT(*) FROM lore').fetchone()[0]
            connection.execute('ROLLBACK')
        else:
            raise AssertionError('复合唯一约束未生效')
        after = connection.execute('SELECT * FROM lore ORDER BY source_id, version').fetchall()
        assert before == after
        assert output['before_explicit_rollback_count'] == 6
        assert len(output['is_null']['rows']) == 1 and output['equals_null']['rows'] == []
        output.update(after_rollback_count=len(after), rollback_restored=True,
                      learner_prediction=LEARNER_PREDICTION or '尚未填写；本运行是 AI 参考',
                      sqlite_version=sqlite3.sqlite_version)
        return output
    finally:
        connection.close()


if __name__ == '__main__':
    print(json.dumps(run_experiment(), ensure_ascii=False, indent=2))
