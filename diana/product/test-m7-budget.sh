#!/usr/bin/env bash
# M7 ERRATA-002: independent budget mutations and real historical falsifiers.
set -uo pipefail
PRODUCT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$PRODUCT_DIR" <<'PY'
import copy, datetime, json, subprocess, sys, tempfile
from pathlib import Path
from unittest.mock import patch

product = Path(sys.argv[1]); repo = product.parent.parent
sys.path.insert(0, str(product))
import proposal as P, refusal as R, catalogue as CAT
import journal as J, runpolicy as RP, view as V

passed = failed = falsifiers = 0
def check(label, condition):
    global passed, failed
    if condition is True:
        passed += 1; print('PASS ', label)
    else:
        failed += 1; print('FAIL ', label, repr(condition))
def falsify(label, condition):
    global falsifiers
    falsifiers += 1; check('[falsifier] '+label, condition)
def refused(fn):
    try:
        fn()
    except R.Refused as e:
        return e.code
    return None

def serial(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))

# Load the committed vulnerable implementation, not a lookalike mock of it.
# All fixtures and run artifacts remain under one disposable temporary root.
old_source = subprocess.run(['git', '-C', str(repo), 'show',
    '709b10e:diana/product/proposal.py'], capture_output=True, text=True, check=True).stdout
old = {'__file__': str(product/'proposal.py'), '__name__': 'historical_proposal'}
exec(compile(old_source, '709b10e:proposal.py', 'exec'), old)

with tempfile.TemporaryDirectory(prefix='m7-e2-budget-') as directory:
    tmp = Path(directory)
    def fresh(name, module=P):
        root=tmp/name/'repo'; (root/'src/core').mkdir(parents=True)
        (root/'src/auth').mkdir(); (root/'tests').mkdir()
        (root/'src/core/x.py').write_text('x=1\n')
        (root/'check.py').write_text('pass\n')
        for args in [('init','-q'),('config','user.email','test@example.invalid'),
                     ('config','user.name','Test'),('add','.'),('commit','-qm','fixture')]:
            subprocess.run(['git','-C',str(root),*args],capture_output=True,check=True)
        base=str(tmp/name/'proposals'); runs=str(tmp/name/'runs')
        build = module['build'] if isinstance(module,dict) else module.build
        doc=build("Fix tests, but don't touch auth or deployment",str(root),base=base)
        path=Path(base)/'proposals'/f"{doc['proposal_digest'].split(':')[1]}.json"
        return doc,path,base,runs

    for field,value in [('max_attempts',999),('total_seconds',999999),
                        ('max_attempts',5),('total_seconds',3599)]:
        label=f'{field}={value}'
        baseline,path,base,runs=fresh(label)
        changed=copy.deepcopy(baseline); changed['budget'][field]=value
        restored=copy.deepcopy(changed); restored['budget'][field]=baseline['budget'][field]
        check(label+' only the selected field changed',serial(restored)==serial(baseline))
        check(label+' changed canonical identity',P.recompute(changed)!=baseline['proposal_digest'])
        path.write_text(serial(changed))
        code=refused(lambda:P.approve(baseline['proposal_digest'],base=base,runs_base=runs))
        check(label+' exact stale refusal',code==R.PROPOSAL_STALE)
        check(label+' zero run authority effects',not Path(runs).exists())
        path.write_text(serial(restored))
        out=P.approve(baseline['proposal_digest'],base=base,runs_base=runs)
        falsify(label+' restored original approves',out['approved'] is True)
        policy=json.loads((Path(out['run_directory'])/'run-policy.json').read_text())
        check(label+' persisted budget matches original',
              P.policy_authority(policy)==P.policy_authority(baseline['predicted_policy']))

        historical,hpath,hbase,hruns=fresh('old-'+label,old)
        historical['budget'][field]=value; hpath.write_text(serial(historical))
        check(label+' historical digest ignores the isolated mutation',
              old['recompute'](historical)==historical['proposal_digest'])
        hout=old['approve'](historical['proposal_digest'],base=hbase,runs_base=hruns)
        hp=json.loads((Path(hout['run_directory'])/'run-policy.json').read_text())
        falsify(label+' real old control accepted changed budget',
                hout['approved'] is True and P.policy_authority(hp)[field]==value)

    baseline,path,base,runs=fresh('delayed')
    rendered=V.plan_view(baseline)
    check('approval view shows actual attempts, duration and grace',
          '6 attempts, 3600 seconds from run creation' in rendered and '20 seconds' in rendered)
    original_approve=P._actors.approve
    original_build=RP.build
    later='2030-01-01T00:00:00Z'
    def delayed(**kwargs):
        def at_later(**args):
            return original_build(**(args | {'created_at':later}))
        with patch.object(RP,'build',at_later):
            return original_approve(**kwargs)
    with patch.object(P._actors,'approve',delayed):
        out=P.approve(baseline['proposal_digest'],base=base,runs_base=runs)
    rd=Path(out['run_directory']); policy=json.loads((rd/'run-policy.json').read_text())
    check('creation can move without changing approved duration',
          policy['created_at']==later and P.policy_authority(policy)==
          P.policy_authority(baseline['predicted_policy']))
    check('journal binds actual absolute policy',J.read(rd)['run_policy_digest']==RP.digest(policy))
    J.transition(rd,J.read(rd),J.ARMED,note='progress before replay')
    before={p.name:p.read_bytes() for p in rd.iterdir() if p.is_file()}
    code=refused(lambda:P.approve(baseline['proposal_digest'],base=base,runs_base=runs))
    check('re-approval refuses an ARMED run',code==R.PROPOSAL_ALREADY_APPROVED)
    check('re-approval leaves every run artifact byte-identical',
          before=={p.name:p.read_bytes() for p in rd.iterdir() if p.is_file()})

    historical,hpath,hbase,hruns=fresh('old-replay',old)
    hout=old['approve'](historical['proposal_digest'],base=hbase,runs_base=hruns)
    hrd=Path(hout['run_directory']); J.transition(hrd,J.read(hrd),J.ARMED,note='old replay proof')
    old['approve'](historical['proposal_digest'],base=hbase,runs_base=hruns)
    falsify('historical re-approval resets ARMED to APPROVED',J.read(hrd)['state']=='APPROVED')

    for field in ('max_attempts','total_seconds','quiescence_grace_seconds','run_id'):
        policy2=copy.deepcopy(policy)
        if field=='total_seconds':
            deadline=RP.parse_iso(policy2['deadline_at'])+datetime.timedelta(seconds=1)
            policy2['deadline_at']=deadline.strftime('%Y-%m-%dT%H:%M:%SZ')
        elif field=='run_id': policy2[field]+='-different'
        else: policy2[field]+=1
        check('digest binds normalized policy '+field,
              P.digest_of(baseline['predicted_contract'],baseline['predicted_items'],
                          baseline['predicted_topology'],policy2)!=baseline['proposal_digest'])
    invalid=copy.deepcopy(policy); invalid['policy_version']=2
    try: P.policy_authority(invalid); rejection_code=None
    except P._runpolicy.blocking.Blocked as exc: rejection_code=exc.code
    check('unknown policy version refuses',rejection_code==RP.blocking.RUN_POLICY_MALFORMED)

    for field in ('max_attempts','total_seconds','quiescence_grace_seconds','policy_version'):
        baseline,path,base,runs=fresh('persist-'+field)
        def substitute(**kwargs):
            result=original_approve(**kwargs); rd=Path(result['run_directory'])
            policy=json.loads((rd/'run-policy.json').read_text())
            if field=='total_seconds':
                policy['deadline_at']=(RP.parse_iso(policy['deadline_at'])+
                    datetime.timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
            else: policy[field]+=1
            (rd/'run-policy.json').write_text(serial(policy))
            rec=J.read(rd); rec['run_policy_digest']=RP.digest(policy); J.write(rd,rec)
            return result
        with patch.object(P._actors,'approve',substitute):
            code=refused(lambda:P.approve(baseline['proposal_digest'],base=base,runs_base=runs))
        falsify('post-create '+field+' substitution aborts',code==R.CONTRACT_PREDICTION_FAILED)
        rd=Path(P._contract.run_dir(baseline['run_id'],runs))
        check('post-create '+field+' has no attempts',J.read(rd)['attempts']==[])

    for field,value in [('max_attempts',True),('max_attempts',6.5),
                        ('total_seconds','3600'),('total_seconds',0)]:
        baseline,path,base,runs=fresh('type-'+field+'-'+str(value))
        baseline['budget'][field]=value; path.write_text(serial(baseline))
        check('invalid budget refuses without coercion '+repr(value),
              refused(lambda:P.approve(baseline['proposal_digest'],base=base,runs_base=runs))
              ==R.PROPOSAL_MALFORMED and not Path(runs).exists())

    for path in ('.git','.github','./.github/workflows','././.git/config',
                 '.github/workflows/build.yml','.git/config','diana/runtime'):
        check('forbidden write '+path,CAT.is_forbidden_write(path))
    falsify('ordinary source path remains writable',not CAT.is_forbidden_write('src/core/x.py'))
    old_catalogue={}
    source=subprocess.run(['git','-C',str(repo),'show','709b10e:diana/product/catalogue.py'],
                          capture_output=True,text=True,check=True).stdout
    exec(compile(source,'709b10e:catalogue.py','exec'),old_catalogue)
    falsify('historical prefix stripping allowed .git and .github',
            not old_catalogue['is_forbidden_write']('.git')
            and not old_catalogue['is_forbidden_write']('.github/workflows'))

print(f'\n{passed} passed, {failed} failed, {falsifiers} falsifiers')
sys.exit(1 if failed else 0)
PY
