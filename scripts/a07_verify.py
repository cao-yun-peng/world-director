"""离线验收与证据清单；保留已有日志，旧演示使用新的 A07 输出路径。"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'PYTHONIOENCODING': 'utf-8'}
    tasks = [
        ('A07-T', 'a07_tests.txt', ['-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_a07*.py', '-v']),
        ('A07-REG', 'a07_all_tests.txt', ['-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        ('A07-DEMO', 'a07_demo_output.txt', ['-m', 'scripts.a07_demo']),
        ('A07-A05', 'a07_a05_output.txt', ['-c', "import asyncio; from pathlib import Path; from scripts.a05_demo import demo; e=asyncio.run(demo(Path('docs/a07_a05_regression.json'))); print([b['checks'] for b in e['branches']]); print(e['fault'])"]),
        ('A07-A06', 'a07_a06_output.txt', ['-c', "import asyncio,json; from pathlib import Path; from scripts.a06_demo import demo; print(json.dumps(asyncio.run(demo(Path('docs/a07_a06_regression.json'))),ensure_ascii=False))"]),
        ('A07-SQL', 'a07_sql_output.txt', ['-m', 'exercises.a07_sql']),
        ('A07-VAR', 'a07_visibility_output.txt', ['-c', "import asyncio,json; from exercises.a07_visibility import run_variant; print(json.dumps([asyncio.run(run_variant(False)),asyncio.run(run_variant(True))],ensure_ascii=False,indent=2))"]),
        ('A07-A06-VAR', 'a07_a06_variants.txt', ['-c', "import asyncio,json; from exercises.a06_replan import run_variant; print(json.dumps([asyncio.run(run_variant(False)),asyncio.run(run_variant(True))],ensure_ascii=False,indent=2))"]),
        ('A07-WAIT', 'a07_wait_output.txt', ['-m', 'scripts.a06_wait_demo']),
    ]
    commands = []
    for evidence_id, filename, arguments in tasks:
        command = [sys.executable, '-X', 'utf8', *arguments]
        started = datetime.now(timezone.utc).isoformat()
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        output = ROOT / 'docs' / filename
        if output.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            output.rename(output.with_name(output.stem + '.previous-' + stamp + output.suffix))
        output.write_text('COMMAND: ' + subprocess.list2cmdline(command) + '\nSTARTED_UTC: ' + started
            + '\nEXIT_CODE: ' + str(result.returncode) + '\n\n' + result.stdout + result.stderr, encoding='utf-8')
        commands.append({'id': evidence_id, 'command': command, 'started_at': started,
                         'exit_code': result.returncode, 'evidence': str(output.relative_to(ROOT))})
        print(f'{evidence_id}: exit={result.returncode}; {output.name}', flush=True)
    diff = subprocess.run(['git', 'diff', '--check'], cwd=ROOT, capture_output=True, text=True)
    commands.append({'id': 'A07-DIFF', 'command': ['git', 'diff', '--check'], 'exit_code': diff.returncode,
                     'output': diff.stdout + diff.stderr})
    hashes = {}
    for pattern in ('app/*.py', 'data/lore/*.json', 'tests/test_a07*.py', 'scripts/a07*.py', 'exercises/a07*.py'):
        for path in sorted(ROOT.glob(pattern)):
            hashes[str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    evidence = {'task': 'A07-IMPLEMENT-20260917', 'revision': 1, 'mode': 'offline',
                'head': head, 'worktree': 'uncommitted A07 implementation', 'commands': commands,
                'sha256_raw_bytes': hashes, 'validity': '代码、数据、测试及配置变化后重新核验',
                'real_retrieval': 'not_run_missing_embedding_config', 'real_generation': 'not_run',
                'learner_independent': 'pending', 'python': sys.version}
    (ROOT / 'docs/a07_evidence_manifest.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(c['exit_code'] == 0 for c in commands) else 1


if __name__ == '__main__':
    raise SystemExit(main())
