"""冻结不含密钥的源码副本验收；新目录保存证据，旧课程工件不覆盖。"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

ROOT=Path(__file__).resolve().parent.parent


def source_files():
    result=[]
    for folder in ('app','scripts','tests','exercises','data'):
        result.extend(p for p in (ROOT/folder).rglob('*')
                      if p.is_file() and p.suffix in ('.py','.json','.jsonl') and '__pycache__' not in p.parts)
    result.append(ROOT/'requirements.txt')
    return sorted(result)


def hashes(files):
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    output=ROOT/'docs/a08_verification'/stamp
    output.mkdir(parents=True)
    files=source_files()
    before=hashes(files)
    commands=[]
    env={**os.environ,'PYTHON_DOTENV_DISABLED':'1','PYTHONIOENCODING':'utf-8'}
    with tempfile.TemporaryDirectory(prefix='a08-frozen-') as temporary:
        frozen=Path(temporary)
        for path in files:
            target=frozen/path.relative_to(ROOT)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,target)
        if any(hashlib.sha256((frozen/rel).read_bytes()).hexdigest()!=value for rel,value in before.items()):
            raise RuntimeError('SOURCE_CHANGED_DURING_FREEZE')
        (frozen/'docs').mkdir()
        with zipfile.ZipFile(output/'source.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                relative=path.relative_to(ROOT)
                archive.write(frozen/relative,relative.as_posix())
        tasks=[
            ('A08-T',['-m','unittest','discover','-s','tests','-p','test_a08*.py','-v']),
            ('A08-ALL',['-m','unittest','discover','-s','tests','-v']),
            ('A08-EVAL',['-m','scripts.a08_eval']),
            ('A08-DEMO',['-m','scripts.a08_demo']),
            ('A08-A07',['-m','scripts.a07_demo']),
            ('A08-A06',['-m','scripts.a06_demo']),
            ('A08-A05',['-m','scripts.a05_demo']),
        ]
        for evidence_id,args in tasks:
            command=[sys.executable,'-X','utf8',*args]
            started=datetime.now(timezone.utc).isoformat()
            begun=monotonic()
            try:
                completed=subprocess.run(command,cwd=frozen,env=env,capture_output=True,
                                         text=True,encoding='utf-8',timeout=180)
                code,text=completed.returncode,completed.stdout+completed.stderr
            except subprocess.TimeoutExpired:
                code,text=124,'TIMEOUT; no pass claim'
            elapsed=round(monotonic()-begun,3)
            filename=evidence_id.lower()+'.txt'
            (output/filename).write_text('COMMAND: '+subprocess.list2cmdline(command)+
                '\nSTARTED_UTC: '+started+'\nEXIT_CODE: '+str(code)+'\nDURATION_S: '+str(elapsed)+
                '\nCWD: frozen source.zip extraction\n\n'+text,encoding='utf-8')
            commands.append({'id':evidence_id,'command':command,'started_at':started,'exit_code':code,
                             'duration_s':elapsed,'evidence':filename})
            print(f'{evidence_id}: exit={code}; {elapsed}s',flush=True)
        for path in (frozen/'docs').glob('*.json'):
            shutil.copyfile(path,output/path.name)
    changed=before!=hashes(files) or set(source_files())!=set(files)
    manifest={'task':'A08-IMPLEMENT-20260917','evidence_id':'A08-FROZEN-'+stamp,
              'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'worktree':'uncommitted A08 plus concurrent A07 real-fix source; see source.zip',
              'python':sys.version,'platform':platform.platform(),
              'sha256_raw_bytes':before,'line_endings':'raw bytes include CRLF/LF; no normalized hash substitution',
              'commands':commands,'source_changed_after_freeze':changed,
              'real_services':'not_run_in_this_verification','learner_independent':'pending',
              'validity':'frozen source.zip only; changed live source needs new verification',
              'source_zip_sha256':hashlib.sha256((output/'source.zip').read_bytes()).hexdigest()}
    manifest['artifacts_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in output.iterdir() if p.is_file() and p.name!='source.zip'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'evidence':str(output),'changed':changed},ensure_ascii=False),flush=True)
    return 0 if not changed and all(c['exit_code']==0 for c in commands) else 1


if __name__=='__main__':
    raise SystemExit(main())
