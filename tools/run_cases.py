"""Dev tool (not used by the grader): run every examples/<name>/case.json N times and summarise.

    python tools/run_cases.py --model deepseek/deepseek-v4.1-flash            # all cases, 2 runs each
    python tools/run_cases.py --model ... --cases pagerank bayes_test --runs 1

Outputs go to runs/<case>_<k>/ (runs/ is git-ignored), with AGENT_DEBUG=1 so each folder
also has spec.json and excerpt_used.txt. Prints a table and writes runs/summary.csv.
Each run starts from a fresh output directory, like the grader.
"""
import argparse
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def summarise(trace_path):
    row = {"calls": "", "prompt_tok": "", "completion_tok": "", "total_tok": "", "secs": "",
           "revisions": "", "result": "no trace", "remaining_failures": "", "first_failures": "",
           "js_expects": "", "quotes": ""}
    if not os.path.exists(trace_path):
        return row
    first_fail = None
    with open(trace_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("action") == "run_all" and d.get("round") == "initial" and d.get("failures"):
                first_fail = d["failures"]
            if d.get("action") == "run_js" and isinstance(d.get("stats"), dict) and d["stats"]:
                st = d["stats"]
                row["js_expects"] = f'{st.get("expects_passed")}/{st.get("expects_total")}'
            if d.get("action") == "verify_quotes":
                row["quotes"] = "; ".join(d.get("notes") or [])[-60:]
            if d.get("stage") == "done":
                p, c = d.get("total_prompt_tokens") or 0, d.get("total_completion_tokens") or 0
                row.update(calls=d.get("calls"), prompt_tok=p, completion_tok=c, total_tok=p + c,
                           secs=d.get("elapsed_s"), revisions=d.get("revisions"),
                           result=d.get("result"),
                           remaining_failures=len(d.get("remaining_failures") or []))
    row["first_failures"] = " | ".join(first_fail or [])[:200]
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--cases", nargs="*", help="case names under examples/ (default: all)")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    names = args.cases or sorted(os.path.basename(os.path.dirname(p))
                                 for p in glob.glob(os.path.join(ROOT, "examples", "*", "case.json")))
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    env = dict(os.environ, AGENT_DEBUG="1")
    rows = []
    for name in names:
        for k in range(1, args.runs + 1):
            out = os.path.join(ROOT, "runs", f"{name}_{k}")
            shutil.rmtree(out, ignore_errors=True)
            t = time.time()
            proc = subprocess.run([sys.executable, os.path.join(ROOT, "agent.py"),
                                   "--input", os.path.join(ROOT, "examples", name, "case.json"),
                                   "--output", out, "--model", args.model],
                                  env=env, capture_output=True, text=True)
            row = {"case": name, "run": k, "exit": proc.returncode, "wall_s": round(time.time() - t, 1)}
            row.update(summarise(os.path.join(out, "trace.jsonl")))
            rows.append(row)
            print(f'{name:<26} run {k}  exit {row["exit"]}  {row["secs"]}s  calls {row["calls"]}  '
                  f'tokens {row["total_tok"]} ({row["prompt_tok"]}+{row["completion_tok"]})  '
                  f'rev {row["revisions"]}  {row["result"]}  js {row["js_expects"]}', flush=True)
            if proc.returncode != 0 and proc.stderr:
                print("   stderr:", proc.stderr.strip()[-300:])

    path = os.path.join(ROOT, "runs", "summary.csv")
    # keep rows of cases not re-run this time (merge by case + run)
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            done = {(r["case"], str(r["run"])) for r in rows}
            old = [r for r in csv.DictReader(f) if (r.get("case"), str(r.get("run"))) not in done]
        rows = sorted(old + rows, key=lambda r: (r["case"], int(r["run"])))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[-1].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if r["exit"] == 0]
    if ok:
        avg = lambda k: sum(float(r[k] or 0) for r in ok) / len(ok)
        print(f"\n{len(ok)}/{len(rows)} runs exit 0 | avg tokens {avg('total_tok'):.0f} | "
              f"avg secs {avg('secs'):.1f} | avg calls {avg('calls'):.2f} | "
              f"runs needing revision {sum(1 for r in ok if (r['revisions'] or 0) > 0)}")
    print("summary:", path)


if __name__ == "__main__":
    main()
