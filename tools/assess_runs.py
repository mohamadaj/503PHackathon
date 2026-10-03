"""
Grade every run folder that A's tools/run_cases.py produced, and build the baseline table (dev only).

    python tools/assess_runs.py                          # all runs/<case>_<k>/ folders
    python tools/assess_runs.py --only entropy conv1d --model deepseek/deepseek-v4-pro --jobs 4
    python tools/assess_runs.py --force                  # re-grade even if assessment.json exists

For each folder: tools/mock_assess.assess() -> <folder>/assessment.json (reused on later runs unless --force,
so re-running only costs money for new folders). A folder without index.html scores 0 (no usable page).
Writes runs/assessment.csv (case, run, per-criterion scores, total, top lost-point reasons, grader cost) and,
joined with A's runs/summary.csv, runs/baseline.csv. Prints the table, averages, the top recurring complaints
(by points lost, grouped by tag) and the money spent.
"""
import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_assess as M  # noqa: E402

ROOT = M.ROOT
CRITERIA = list(M.MAX)
GEN_MODEL = "deepseek/deepseek-v4.1-flash"


def grade_folder(folder, model, force):
    out = folder / "assessment.json"
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8")), "cached"
    if not (folder / "index.html").exists():
        return None, "no page"
    case = json.loads(M.case_for_run(folder).read_text(encoding="utf-8"))
    g = M.assess(case, folder / "index.html", folder / "trace.jsonl", None, model)
    out.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    return g, "graded"


def top_lost(g, n=3):
    lost = [(x["points"], k, x["reason"]) for k, v in g["scores"].items() for x in v["lost"]]
    return " || ".join(f"-{p} {k}: {r[:110]}" for p, k, r in sorted(lost, key=lambda t: -t[0])[:n])


def gen_price(model):
    """(USD per prompt token, per completion token) from OpenRouter's public model list; None if unknown."""
    try:
        data = requests.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]
        p = next(m["pricing"] for m in data if m["id"] == model)
        return float(p["prompt"]), float(p["completion"])
    except Exception:  # noqa: BLE001
        return None


def read_summary(runs_dir):
    path = runs_dir / "summary.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {(r["case"], str(r["run"])): r for r in csv.DictReader(f)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--only", nargs="*", help="case names to grade (default: all folders)")
    ap.add_argument("--model", default=M.DEFAULT_MODEL, help="grader model")
    ap.add_argument("--gen-model", default=GEN_MODEL, help="generator model, for the cost estimate")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    runs_dir = Path(a.runs_dir)
    folders = sorted(p for p in runs_dir.iterdir() if p.is_dir() and re.search(r"_\d+$", p.name))
    if a.only:
        folders = [p for p in folders if re.sub(r"_\d+$", "", p.name) in a.only]
    if not folders:
        sys.exit(f"no run folders like <case>_<k> in {runs_dir}")

    def job(folder):
        try:
            return folder, *grade_folder(folder, a.model, a.force)
        except Exception as e:  # noqa: BLE001  (one bad folder must not stop the batch)
            return folder, None, f"error: {type(e).__name__}: {e}"[:200]

    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        results = list(pool.map(job, folders))

    summary = read_summary(runs_dir)
    rows, grader_cost, complaints = [], 0.0, defaultdict(lambda: {"points": 0, "runs": set(), "example": ""})
    for folder, g, status in results:
        case, run = re.sub(r"_\d+$", "", folder.name), folder.name.rsplit("_", 1)[1]
        row = {"case": case, "run": run, "status": status}
        if g:
            row.update({k: g["scores"][k]["score"] for k in CRITERIA})
            row.update(total=g["quality_total"], top_lost=top_lost(g), grader_usd=g["grader"].get("cost_usd") or 0)
            grader_cost += row["grader_usd"] if status == "graded" else 0
            for k, v in g["scores"].items():
                for x in v["lost"]:
                    c = complaints[x["tag"]]
                    c["points"] += x["points"]
                    c["runs"].add(folder.name)
                    if x["points"] >= c.get("best", 0):
                        c["best"], c["example"] = x["points"], f"{case}: {x['reason'][:160]}"
        else:
            row.update({k: 0 for k in CRITERIA}, total=0, top_lost=status, grader_usd=0)
        s = summary.get((case, run), {})
        row.update(tokens=s.get("total_tok", ""), prompt_tok=s.get("prompt_tok", ""), completion_tok=s.get("completion_tok", ""),
                   secs=s.get("secs", ""), calls=s.get("calls", ""), revisions=s.get("revisions", ""),
                   check_failures=s.get("remaining_failures", ""), exit=s.get("exit", ""))
        rows.append(row)

    cols_a = ["case", "run", *CRITERIA, "total", "top_lost", "grader_usd", "status"]
    cols_b = ["case", "run", "tokens", "secs", "calls", "revisions", "check_failures", *CRITERIA, "total", "top_lost"]
    for name, cols in (("assessment.csv", cols_a), ("baseline.csv", cols_b)):
        with open(runs_dir / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    print(f"{'case':26} run {'tokens':>7} {'secs':>6} calls rev fails | " + " ".join(f"{k[:5]:>5}" for k in CRITERIA) + "  total")
    for r in rows:
        print(f"{r['case']:26} {r['run']:>3} {str(r['tokens']):>7} {str(r['secs']):>6} {str(r['calls']):>5} {str(r['revisions']):>3} "
              f"{str(r['check_failures']):>5} | " + " ".join(f"{r[k]:>5}" for k in CRITERIA) + f"  {r['total']:>5}"
              + ("" if r["status"] in ("graded", "cached") else f"  ({r['status']})"))
    n = len(rows)
    print(f"\naverage quality {sum(r['total'] for r in rows) / n:.1f}/85 over {n} runs; "
          f"below 50: {sum(r['total'] < 50 for r in rows)}")
    toks = [float(r["tokens"]) for r in rows if str(r["tokens"]).strip()]
    if toks:
        print(f"average tokens {sum(toks) / len(toks):.0f}")

    print("\nTop recurring complaints (points lost across all runs, by tag):")
    for tag, c in sorted(complaints.items(), key=lambda kv: -kv[1]["points"])[:5]:
        print(f"  {tag:22s} -{c['points']:>3} pts in {len(c['runs'])} run(s); e.g. {c['example']}")

    price = gen_price(a.gen_model)
    gen_cost = None
    if price:
        gen_cost = sum(float(r["prompt_tok"] or 0) * price[0] + float(r["completion_tok"] or 0) * price[1] for r in rows)
    print(f"\nmoney: grader ${grader_cost:.4f} this run (cached grades cost nothing again)"
          + (f"; generation ≈ ${gen_cost:.4f} at {a.gen_model} list price" if gen_cost is not None else ""))
    print(f"written: {runs_dir / 'assessment.csv'}, {runs_dir / 'baseline.csv'}")


if __name__ == "__main__":
    main()
