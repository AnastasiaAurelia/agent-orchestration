#!/usr/bin/env bash
# Exercise the exact instruction printed by the real product, never reconstruct it.
set -uo pipefail
PRODUCT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$PRODUCT_DIR" <<'PY'
import copy, json, os, re, shlex, subprocess, sys, tempfile
from pathlib import Path

product=Path(sys.argv[1]); cli=product.parent.parent/'diana-do'
passed=failed=0
def check(label, condition):
    global passed,failed
    if condition is True: passed+=1; print('PASS ',label,flush=True)
    else: failed+=1; print('FAIL ',label,repr(condition),flush=True)

def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}

with tempfile.TemporaryDirectory(prefix='m7-copy-paste-') as t:
    tmp=Path(t); origin=tmp/'origin'; origin.mkdir(); other=tmp/'other cwd'; other.mkdir()
    env=dict(os.environ); env['DIANA_RUNS_BASE']=str(tmp/'runs')
    env['DIANA_PRODUCT_SCRIPTED_EDITS']=json.dumps({'item-1':['src/core/calc.py','def add(a,b): return a+b\n']})
    # No source-checkout PATH injection or alias. The instruction must resolve itself.
    env['PATH']=os.pathsep.join(p for p in env['PATH'].split(os.pathsep) if Path(p or '.').resolve()!=cli.parent)
    def fresh(name):
        root=origin/(name+" target ' space"); (root/'src/core').mkdir(parents=True)
        (root/'src/auth').mkdir(); (root/'tests').mkdir()
        (root/'src/core/calc.py').write_text('def add(a,b): return a-b\n')
        (root/'src/auth/keep.py').write_text('unchanged=1\n')
        (root/'check.py').write_text('import sys\nsys.path.insert(0,"src")\nfrom core.calc import add\nassert add(2,3)==5\n')
        for a in [('init','-q'),('config','user.email','test@example.invalid'),
                  ('config','user.name','Test'),('add','.'),('commit','-qm','fixture')]:
            subprocess.run(['git','-C',str(root),*a],capture_output=True,check=True)
        # Relative custom storage and quoted paths must survive a different cwd.
        base=name+" proposals ' space"
        result=subprocess.run([str(cli),"fix the failing tests in this repo, but don't touch auth",
            '--repo',str(root),'--proposals-base',base,'--executor','deterministic'],
            cwd=origin,env=env,capture_output=True,text=True)
        check(name+' proposal succeeds',result.returncode==0)
        commands=[line.split('To start it:',1)[1].strip() for line in result.stdout.splitlines() if 'To start it:' in line]
        check(name+' exactly one printed instruction',len(commands)==1)
        command=commands[0]
        tokens=shlex.split(command); digest=tokens[tokens.index('approve')+1]
        files=list((origin/base/'proposals').glob('*.json'))
        check(name+' exactly one persisted proposal',len(files)==1)
        doc=json.loads(files[0].read_text())
        check(name+' displayed token matches persisted identity',
              re.fullmatch(r'sha256:[0-9a-f]{64}',digest) is not None
              and doc['proposal_digest']==digest and files[0].stem==digest.split(':')[1]
              and any(line.strip()==digest for line in result.stdout.splitlines()))
        return root,files[0],doc,command
    def execute(command):
        # Positive tests execute the captured string itself, unchanged.
        return subprocess.run(command,shell=True,executable='/bin/bash',cwd=other,
                              env=env,capture_output=True,text=True,timeout=180)
    def refuses(label, command, code, docs):
        before=snapshot(tmp/'runs')
        result=execute(command)
        check(label+' exact refusal',result.returncode==2 and '['+code+']' in result.stderr)
        check(label+' no run authority effects',snapshot(tmp/'runs')==before
              and all(not (tmp/'runs'/d['run_id']).exists() for d in docs))

    root,path,doc,command=fresh('verbatim')
    auth_before=(root/'src/auth/keep.py').read_bytes()
    result=execute(command)
    check('unmodified printed command works from a different cwd',result.returncode==0 and 'APPROVED  run '+doc['run_id'] in result.stdout)
    rd=tmp/'runs'/doc['run_id']
    if not (rd/'journal.json').exists():
        print('Printed command:',command,'\nActual stderr:',result.stderr,flush=True)
        sys.exit(1)
    record=json.loads((rd/'journal.json').read_text())['record']
    contract=json.loads((rd/'contract.json').read_text())
    check('real bounded M6 run completes',record['state']=='COMPLETE' and bool(record['attempts']))
    check('real builder and reviewer ran',{'BUILDER','REVIEWER'} <= {a.get('actor') for a in record['attempts']})
    check('only intended target was repaired','return a+b' in (root/'src/core/calc.py').read_text()
          and (root/'src/auth/keep.py').read_bytes()==auth_before and list(other.iterdir())==[])
    policy=json.loads((rd/'run-policy.json').read_text())
    check('approved attempt budget preserved',policy['max_attempts']==doc['budget']['max_attempts'])

    root,path,doc,command=fresh('corrupt')
    digest=doc['proposal_digest']; corrupt=digest[:-1]+('0' if digest[-1]!='0' else '1')
    refuses('one-character corruption',command.replace(digest,corrupt),'proposal-not-found',[doc])
    for value in ('yes','do it','continue',digest.split(':')[1],digest+'ab',
                  digest[:-1]+'g',digest.upper(),digest+' ',
                  'sha256:778c9284a207e6acbe184578228cdee82d6ca1c783e0ecf0fb8cfcd787949ebcb8'):
        refuses('noncanonical '+repr(value),command.replace(digest,shlex.quote(value)),
                'approval-not-a-digest',[doc])

    root2,path2,doc2,command2=fresh('other-proposal')
    # B's stored object is made available at A's lookup location to prove that
    # lookup success alone cannot authorize a proposal for the wrong target.
    (path.parent/path2.name).write_bytes(path2.read_bytes())
    refuses('another proposal digest with original target',command.replace(digest,doc2['proposal_digest']),
            'approval-digest-mismatch',[doc,doc2])
    parts=shlex.split(command); parts[parts.index('--repo')+1]=str(root2)
    refuses('explicit wrong repository',shlex.join(parts),'approval-digest-mismatch',[doc,doc2])
    parts=shlex.split(command); idx=parts.index('--repo'); repo_args=parts[idx:idx+2]; del parts[idx:idx+2]
    parts[1:1]=['--repo',str(root2)]
    refuses('wrong repository before verb',shlex.join(parts),'approval-digest-mismatch',[doc,doc2])

    # A repository named with an unexpanded `~` must still match: the contract
    # stores read_scope.canonicalize(...), which expands it, so comparing with a
    # bare realpath refused a correct repository. Driven for real by scoping HOME
    # to the fixture tree, so `~` genuinely resolves to the approved repository.
    root,path,doc,command=fresh('tilde')
    parts=shlex.split(command)
    repo_arg=parts[parts.index('--repo')+1]
    # Scope HOME to the fixture so `~` resolves into it, but pin Hermes
    # explicitly: the launcher defaults HERMES_HOME to $HOME/.hermes, and
    # moving HOME would otherwise hide the real installation.
    tilde_env=dict(env); tilde_env['HOME']=str(origin)
    tilde_env['DIANA_HERMES_HOME']=env.get('DIANA_HERMES_HOME',
        os.path.join(os.path.expanduser('~'),'.hermes','hermes-agent'))
    parts[parts.index('--repo')+1]='~/'+os.path.relpath(repo_arg,str(origin))
    tilde_result=subprocess.run(shlex.join(parts),shell=True,executable='/bin/bash',
                                cwd=other,env=tilde_env,capture_output=True,text=True,timeout=180)
    check('an unexpanded ~ repository is accepted, not refused as a mismatch',
          tilde_result.returncode==0 and 'APPROVED  run '+doc['run_id'] in tilde_result.stdout)
    root2t,path2t,doc2t,command2t=fresh('tilde-wrong')
    parts=shlex.split(command2t)
    parts[parts.index('--repo')+1]='~/'+os.path.relpath(str(root),str(origin))
    wrong_tilde=subprocess.run(shlex.join(parts),shell=True,executable='/bin/bash',
                               cwd=other,env=tilde_env,capture_output=True,text=True,timeout=180)
    check('a ~ path naming the WRONG repository is still refused',
          wrong_tilde.returncode==2 and '[approval-digest-mismatch]' in wrong_tilde.stderr)

    root,path,doc,command=fresh('stale')
    subprocess.run(['git','-C',str(root),'commit','--allow-empty','-qm','moved'],check=True,capture_output=True)
    refuses('stale target',command,'proposal-stale',[doc])

    for field,value in (('max_attempts',999),('total_seconds',999999),('goal','different task')):
        root,path,doc,command=fresh('tamper-'+field)
        changed=copy.deepcopy(doc)
        if field=='goal': changed['intent']['goal']=value
        else: changed['budget'][field]=value
        path.write_text(json.dumps(changed))
        refuses('isolated tamper '+field,command,'proposal-stale',[doc])
        path.write_text(json.dumps(doc))
        result=execute(command)
        check('restoring '+field+' makes same printed command work',
              result.returncode==0 and 'APPROVED  run '+doc['run_id'] in result.stdout)

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
PY
