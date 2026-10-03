"""
Mock assessor (dev only, never used by the agent): grade one generated page with the hackathon rubric,
using a stronger model than the generator through OpenRouter.

    python tools/mock_assess.py --run runs/entropy_1                      # case found from the folder name
    python tools/mock_assess.py --run runs/x_1 --case examples/x/case.json --model deepseek/deepseek-v4-pro
    python tools/mock_assess.py --spec golden/entropy.json --case examples/entropy/case.json   # renders it first

Evidence sent to the grader (see tools/assessor_prompt.md): the case (with the FULL excerpt), the page's visible
text from headless Chrome, the spec (compute code, controls, visuals, explorations), a fresh checks.check_report,
and a compact trace. Writes <run>/assessment.json (or --out) and prints the scores.
Needs OPENROUTER_API_KEY in the environment; the key is never printed or written.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import checks  # noqa: E402

DEFAULT_MODEL = "openai/gpt-5-mini"
MAX = {"accuracy": 25, "clarity": 20, "visual": 15, "interaction": 15, "autonomy": 10}
URL = "https://openrouter.ai/api/v1/chat/completions"
EXCERPT_KEYS = ("excerpt", "text", "source_text", "content", "paper_excerpt", "context")  # as agent.py
MAX_EXCERPT_CHARS = 9000
MAX_PAGE_CHARS = 14000


# ---------------------------------------------------------------- evidence

def find_chrome():
    try:
        sys.path.insert(0, str(ROOT / "tests"))
        from browser_check import find_chrome as b_find  # B's helper
        return b_find()
    except (ImportError, SystemExit):
        return None


class _VisibleText(HTMLParser):
    """Visible text of a rendered page, with section headings kept so the grader can navigate."""
    SKIP = {"script", "style", "noscript", "svg", "template", "title"}
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section", "header", "footer", "label", "button",
             "blockquote", "table", "ul", "ol", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.out, self.hidden = 0, [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP or self.skip:
            self.skip += tag not in ("br", "img", "input")
            return
        if "hidden" in a:
            self.hidden += 1 if tag not in ("br", "img", "input") else 0
        if tag in ("h1", "h2", "h3"):
            self.out.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "input" and a.get("value") is not None and a.get("type") not in ("hidden", "button"):
            self.out.append(f" [{a.get('aria-label') or a.get('type')}: {a['value']}] ")
        if tag == "select":
            self.out.append(" [select] ")
        if tag in ("sub", "sup"):  # keep the formatting visible to the grader: θ<sup>2</sup> -> θ², else θ^(…)
            self.out.append("\x00" + tag + "\x01")

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
            return
        if tag in ("td", "th"):  # keep table columns apart, or the grader misreads them
            self.out.append(" | ")
        elif tag in ("sub", "sup"):
            self.out.append("\x02")
        elif tag == "span":  # chips and inline pieces are separated by CSS on the page
            self.out.append(" ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(data)

    def text(self):
        t = "".join(self.out)
        while "\x00" in t:  # innermost first, so nested d<sub>model</sub> inside a <sup> works
            t = re.sub(r"\x00(sub|sup)\x01([^\x00\x02]*)\x02", _sub_sup, t)
        t = re.sub(r"[ \t]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n", t).strip()


def _sub_sup(m):
    """Unicode sub/superscript when every character has one (pᵢ, x², QKᵀ), else _(…) / ^(…)."""
    from spec_schema import _UNI
    table, body = _UNI[m.group(1)], m.group(2)
    if body and all(ch in table for ch in body):
        return "".join(table[ch] for ch in body)
    return ("_(" if m.group(1) == "sub" else "^(") + body + ")"


def page_evidence(html_path):
    """(visible text, body attributes) of the page after its scripts ran; ('', {}) if no Chrome."""
    chrome = find_chrome()
    if not chrome:
        return "(Chrome not found: no rendered page text)", {}
    proc = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--virtual-time-budget=4000", "--dump-dom",
                           Path(html_path).resolve().as_uri()],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    dom = proc.stdout
    body = re.search(r"<body([^>]*)>", dom)
    attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', body.group(1))) if body else {}
    p = _VisibleText()
    p.feed(dom)
    return p.text(), attrs


def spec_from_html(html):
    m = re.search(r'<script type="application/json" id="spec">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def trace_digest(trace_path, max_lines=60):
    """One short line per trace event (stage/action/result + the most telling fields)."""
    if not trace_path or not Path(trace_path).exists():
        return "(no trace)"
    lines = []
    for raw in Path(trace_path).read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        keep = {k: d[k] for k in ("round", "prompt_tokens", "completion_tokens", "elapsed_s", "failed", "failures",
                                  "errors", "notes", "revisions", "result", "calls", "error") if k in d}
        lines.append(f"{d.get('stage')}/{d.get('action')}: {d.get('result')} {json.dumps(keep, ensure_ascii=False)[:300]}")
    if len(lines) > max_lines:
        lines = lines[:max_lines // 2] + [f"... {len(lines) - max_lines} lines omitted ..."] + lines[-max_lines // 2:]
    return "\n".join(lines)


def case_excerpt(case):
    for k in EXCERPT_KEYS:
        if isinstance(case.get(k), str) and case[k].strip():
            return case[k].strip()
    return ""


# ---------------------------------------------------------------- grading

def build_messages(case, page_text, spec, report, trace):
    system = (ROOT / "tools" / "assessor_prompt.md").read_text(encoding="utf-8")
    excerpt = case_excerpt(case)
    if len(excerpt) > MAX_EXCERPT_CHARS:
        excerpt = excerpt[:MAX_EXCERPT_CHARS] + "\n[... excerpt truncated for the grader ...]"
    brief = {k: case.get(k) for k in ("source_url", "title", "audience", "focus") if case.get(k)}
    user = "\n\n".join([
        "## CASE (the brief the agent received)\n" + json.dumps(brief, ensure_ascii=False, indent=1),
        "## SOURCE EXCERPT (supplied with the case)\n" + (excerpt or "(no excerpt was supplied)"),
        "## PAGE TEXT (rendered at default inputs)\n" + page_text[:MAX_PAGE_CHARS],
        "## SPEC\n" + json.dumps(spec, ensure_ascii=False, separators=(",", ":")),
        "## CHECK REPORT\n" + json.dumps(report, ensure_ascii=False, separators=(",", ":"))[:6000],
        "## TRACE\n" + trace,
        "Grade the page now. Return only the JSON object.",
    ])
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_model(messages, model, max_tokens=6000):
    """(text, info) where info has tokens, cost (USD, from OpenRouter), provider, seconds."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    body = {"model": model, "messages": messages, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"}, "usage": {"include": True}}
    t = time.time()
    for attempt in range(3):
        r = requests.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=240)
        if r.status_code == 200:
            break
        if r.status_code == 400 and "response_format" in body:  # model without JSON mode
            body.pop("response_format")
            continue
        if attempt == 2 or r.status_code < 500 and r.status_code != 429:
            raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(3 * (attempt + 1))
    d = r.json()
    usage = d.get("usage") or {}
    info = {"model": model, "provider": d.get("provider"), "seconds": round(time.time() - t, 1),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost_usd": usage.get("cost")}
    return (d["choices"][0]["message"].get("content") or ""), info


def _first_json_object(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for j in range(start, len(text)):
            c = text[j]
            if in_str:
                esc, in_str = (False, in_str) if esc else (c == "\\", c != '"')
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:j + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def normalize_grade(raw):
    """Clamp scores and make the itemized deductions authoritative: score = max - sum(lost points)."""
    out, notes = {}, []
    for k, mx in MAX.items():
        c = raw.get(k) if isinstance(raw.get(k), dict) else {}
        lost = [x for x in c.get("lost") or [] if isinstance(x, dict) and isinstance(x.get("points"), (int, float))]
        lost = [{"points": int(round(x["points"])), "tag": str(x.get("tag") or "other"), "reason": str(x.get("reason") or "")}
                for x in lost if x["points"] > 0]
        deducted = min(mx, sum(x["points"] for x in lost))
        stated = c.get("score")
        score = mx - deducted
        if isinstance(stated, (int, float)) and int(stated) != score:
            notes.append(f"{k}: model said {stated}, itemized deductions give {score}")
        out[k] = {"score": score, "max": mx, "lost": lost}
    total = sum(v["score"] for v in out.values())
    return {"scores": out, "quality_total": total, "below_50": total < 50,
            "top_fixes": [str(x) for x in (raw.get("top_fixes") or [])][:3], "consistency_notes": notes}


def assess(case, html_path, trace_path=None, spec=None, model=DEFAULT_MODEL):
    html = Path(html_path).read_text(encoding="utf-8")
    spec = spec or spec_from_html(html) or {}
    text, attrs = page_evidence(html_path)
    report = checks.check_report(json.loads(json.dumps(spec)))
    report.pop("lint", None)
    messages = build_messages(case, text, spec, report, trace_digest(trace_path))
    reply, info = call_model(messages, model)
    raw = _first_json_object(reply)
    if raw is None:
        raise RuntimeError(f"grader did not return JSON: {reply[:200]!r}")
    grade = normalize_grade(raw)
    grade["grader"] = info
    grade["page"] = {"booted": attrs.get("data-ready") == "1", "self_checks": attrs.get("data-checks")}
    return grade


def case_for_run(run_dir):
    name = re.sub(r"_\d+$", "", Path(run_dir).name)
    return ROOT / "examples" / name / "case.json"


def print_grade(g, label=""):
    s = g["scores"]
    print(f"{label}quality {g['quality_total']}/85  " + "  ".join(f"{k} {v['score']}/{v['max']}" for k, v in s.items())
          + f"  | ${g['grader'].get('cost_usd') or 0:.4f}, {g['grader']['seconds']} s, {g['grader'].get('provider')}")
    for k, v in s.items():
        for x in v["lost"]:
            print(f"   -{x['points']} {k} [{x['tag']}] {x['reason'][:180]}")
    for f in g["top_fixes"]:
        print(f"   fix: {f[:200]}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", help="run folder with index.html and trace.jsonl (e.g. runs/entropy_1)")
    ap.add_argument("--spec", help="grade a spec file instead: it is rendered with render.py first")
    ap.add_argument("--case", help="case.json (default: examples/<run name without _k>/case.json)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", help="where to write the assessment JSON")
    a = ap.parse_args()
    if a.spec:
        from render import render_html
        spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        tmp = Path(tempfile.mkdtemp(prefix="p2p_assess_")) / "index.html"
        tmp.write_text(render_html(spec), encoding="utf-8")
        html_path, trace_path, out = tmp, None, Path(a.out) if a.out else tmp.with_name("assessment.json")
    elif a.run:
        html_path, trace_path = Path(a.run) / "index.html", Path(a.run) / "trace.jsonl"
        spec, out = None, Path(a.out) if a.out else Path(a.run) / "assessment.json"
    else:
        ap.error("give --run or --spec")
    case_path = Path(a.case) if a.case else case_for_run(a.run or "")
    case = json.loads(case_path.read_text(encoding="utf-8"))
    g = assess(case, html_path, trace_path, spec, a.model)
    out.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    print_grade(g)
    print(f"written: {out}")


if __name__ == "__main__":
    main()
