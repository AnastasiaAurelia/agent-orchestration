---
description: Summarize local Claude Code spend by day, model, and session from the Diana cost log.
argument-hint: [csv]
---

# Cost Report

Adapted from ECC's `cost-report` command. Reads the log written by the Stop
hook in `hooks/hooks.json`, at `~/.claude/diana/costs.jsonl`.

## Where the data lives

The Stop hook appends one JSON row per session-stop. Each row is a
**cumulative snapshot for that session** (it re-parses the whole transcript
each time), so the report takes the **latest row per `session_id`** and sums
across sessions — summing every row would multiply-count.

Row schema:
`{ timestamp, session_id, cwd, model, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, estimated_cost_usd }`

`estimated_cost_usd` is computed from a small hardcoded price table in the
hook (edit `hooks/hooks.json` if pricing changes) — it's an approximation,
not a billing-accurate figure.

## What this command does

1. Check that `~/.claude/diana/costs.jsonl` exists. If not, say the log isn't
   populated yet (it appears after the first session ends with the Stop hook
   installed).
2. Reduce rows to the latest snapshot per `session_id` and aggregate.
3. Print a compact report, or export recent rows as CSV when the argument is
   `csv`.

`node` is used (not `jq`/`sqlite3`) so this works the same on macOS, Linux,
and Windows.

## Report

```bash
node -e '
const fs=require("fs"),os=require("os"),path=require("path");
const f=path.join(os.homedir(),".claude","diana","costs.jsonl");
if(!fs.existsSync(f)){console.log("No cost log yet: "+f+" not found. It populates after your first session ends with the Diana Stop hook installed.");process.exit(0);}
const rows=fs.readFileSync(f,"utf8").split(/\r?\n/).filter(Boolean).map(l=>{try{return JSON.parse(l)}catch{return null}}).filter(Boolean);
const bySession=new Map();
for(const r of rows){const k=r.session_id||r.timestamp;const p=bySession.get(k);if(!p||String(r.timestamp)>String(p.timestamp))bySession.set(k,r);}
const latest=[...bySession.values()];
const cost=r=>Number(r.estimated_cost_usd)||0;
const day=r=>String(r.timestamp||"").slice(0,10);
const today=new Date().toISOString().slice(0,10);
const d=new Date(Date.now()-864e5).toISOString().slice(0,10);
const sum=a=>a.reduce((s,r)=>s+cost(r),0);
const f4=n=>"$"+n.toFixed(4);
console.log("=== Cost summary ===");
console.log("today:     "+f4(sum(latest.filter(r=>day(r)===today))));
console.log("yesterday: "+f4(sum(latest.filter(r=>day(r)===d))));
console.log("total:     "+f4(sum(latest))+"  ("+latest.length+" sessions)");
const by=(key)=>{const m=new Map();for(const r of latest){const k=key(r)||"(unknown)";m.set(k,(m.get(k)||0)+cost(r));}return [...m.entries()].sort((a,b)=>b[1]-a[1]);};
console.log("\n=== By model ===");for(const [k,v] of by(r=>r.model))console.log(f4(v).padStart(12)+"  "+k);
console.log("\n=== By project ===");for(const [k,v] of by(r=>r.cwd))console.log(f4(v).padStart(12)+"  "+k);
console.log("\n=== Last 7 days ===");
const days=new Map();for(const r of latest){const k=day(r);days.set(k,(days.get(k)||0)+cost(r));}
[...days.entries()].sort((a,b)=>b[0]<a[0]?-1:1).slice(0,7).forEach(([k,v])=>console.log(k+"  "+f4(v)));
'
```

## CSV export (`/cost-report csv`)

```bash
node -e '
const fs=require("fs"),os=require("os"),path=require("path");
const f=path.join(os.homedir(),".claude","diana","costs.jsonl");
if(!fs.existsSync(f)){console.error("no data");process.exit(0);}
const rows=fs.readFileSync(f,"utf8").split(/\r?\n/).filter(Boolean).map(l=>{try{return JSON.parse(l)}catch{return null}}).filter(Boolean).slice(-100);
console.log("timestamp,session_id,cwd,model,input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,estimated_cost_usd");
for(const r of rows)console.log([r.timestamp,r.session_id,r.cwd,r.model,r.input_tokens,r.output_tokens,r.cache_creation_input_tokens,r.cache_read_input_tokens,r.estimated_cost_usd].join(","));
'
```

Rely on the precomputed `estimated_cost_usd` from the hook; do not
re-estimate pricing here.
