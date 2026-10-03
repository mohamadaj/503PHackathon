"""Paper to Playground agent: entry point and main loop (Person A).

    python agent.py --input case.json --output out --model MODEL_ID

Pipeline: setup -> context -> generate -> extract -> check -> (revise -> check)* -> render -> exit
Writes out/index.html and out/trace.jsonl. Exit 0 if a page was written from a spec.
"""
import argparse
import copy
import html
import json
import os
import re
import sys
import traceback

from compress import compress
from extract import extract_patch, extract_spec
from llm import LLM
from tracer import Tracer

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_FIELDS = ("source_url", "focus", "audience")
EXCERPT_KEYS = ("excerpt", "text", "source_text", "content", "paper_excerpt", "context")
EXCERPT_BUDGET_CHARS = 6000   # ~1.5k tokens; relevant parts only (see compress.py)
GEN_MAX_TOKENS = 12000
REV_MAX_TOKENS = 5000
MAX_REVISIONS = 2

DISCLAIMER = ("This page is an illustration of the cited idea. "
              "It does not reproduce the paper's experiments or results.")


# ----------------------------------------------------------------- helpers
def read_text(rel):
    with open(os.path.join(HERE, rel), encoding="utf-8") as f:
        return f.read()


def fill(template, **values):
    """Simple {{name}} substitution (no str.format: prompts contain JSON braces)."""
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def load_case(path, tr):
    with open(path, encoding="utf-8") as f:
        case = json.load(f)
    if not isinstance(case, dict):
        raise ValueError("case.json must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS
               if not isinstance(case.get(k), str) or not case[k].strip()]
    tr.log("load", "read_case", "ok" if not missing else "missing_fields",
           fields=sorted(case.keys()), missing=missing)
    if missing:
        raise ValueError(f"missing required fields: {missing}")
    return case


def build_context(case, tr):
    """Excerpt if present; otherwise focus + model knowledge. No network fetching:
    during assessment only OpenRouter is reachable."""
    excerpt, excerpt_key = "", None
    for k in EXCERPT_KEYS:
        if isinstance(case.get(k), str) and case[k].strip():
            excerpt, excerpt_key = case[k].strip(), k
            break
    if not excerpt:
        # Unknown field name: treat the longest extra text field (>=300 chars) as the excerpt.
        longs = [(len(v), k) for k, v in case.items()
                 if k not in REQUIRED_FIELDS and isinstance(v, str) and len(v.strip()) >= 300]
        if longs:
            excerpt_key = max(longs)[1]
            excerpt = case[excerpt_key].strip()
    extras = {k: v for k, v in case.items()
              if k not in REQUIRED_FIELDS and k != excerpt_key
              and isinstance(v, (str, int, float)) and str(v).strip()}
    if excerpt:
        cut, info = compress(excerpt, case["focus"], EXCERPT_BUDGET_CHARS)
        tr.log("context", "compress_excerpt", "ok", field=excerpt_key, **info)
    else:
        cut = ""
        tr.log("context", "no_excerpt", "ok",
               note="no excerpt in case; relying on focus text and model knowledge")
    extra_txt = "\n".join(f"{k}: {str(v)[:500]}" for k, v in extras.items())
    if not cut:
        cut = ("(None. Use only well-established facts about this paper/concept, cite "
               "section/equation names only if confident, and leave source.supported empty.)")
    return cut, extra_txt, bool(excerpt)


# ----------------------------------------------------------------- checks
SPEC_FIELDS = {"title", "subtitle", "audience", "intro", "controls", "presets", "compute",
               "visuals", "explorations", "limitations", "tests", "source"}


def normalize(spec, case, has_excerpt):
    """Fix things the agent knows better than the model (contract v1, see spec_contract.md)."""
    if "compute_js" in spec and "compute" not in spec:
        spec["compute"] = spec.pop("compute_js")
    if isinstance(spec.get("limitations"), str):
        spec["limitations"] = [spec["limitations"]]
    if not spec.get("limitations") and isinstance(spec.get("limitation"), str):
        spec["limitations"] = [spec.pop("limitation")]
    spec.setdefault("audience", case.get("audience", ""))
    src = spec.get("source")
    if not isinstance(src, dict):
        src = spec["source"] = {}
    src["url"] = case["source_url"]  # never trust the model with the URL
    if not has_excerpt:
        src["context_note"] = ("No paper excerpt was supplied; this page is based on the brief "
                               "and well-established knowledge of the paper.")
    return spec


def core_checks(spec):
    """Structural checks owned by the pipeline, so the loop works even if checks.py breaks."""
    if not isinstance(spec, dict):
        return ["spec: not a JSON object"]
    fails = []
    js = spec.get("compute")
    if not isinstance(js, str) or not re.search(r"function\s*\w*\s*\(", js):
        fails.append("compute: missing JS function compute(state)")
    ctrls = spec.get("controls")
    if not isinstance(ctrls, list) or len(ctrls) < 2:
        fails.append("controls: need at least 2 controls")
    elif any(not isinstance(c, dict) or not c.get("id") or not c.get("type") for c in ctrls):
        fails.append("controls: every control needs id and type")
    if not isinstance(spec.get("visuals"), list) or not spec["visuals"]:
        fails.append("visuals: need at least 1 visual")
    exps = spec.get("explorations")
    if not isinstance(exps, list) or len(exps) != 2:
        fails.append("explorations: need exactly 2 explorations")
    elif not any(isinstance(e, dict) and e.get("kind") == "limitation" for e in exps):
        fails.append("explorations: one exploration must have kind \"limitation\"")
    if not isinstance(spec.get("limitations"), list) or not spec["limitations"]:
        fails.append("limitations: need at least 1 limitation/assumption/misconception")
    if not isinstance(spec.get("tests"), list) or len(spec["tests"]) < 2:
        fails.append("tests: need at least 2 tests")
    src = spec.get("source") or {}
    if not src.get("paper") or not (src.get("section") or src.get("equation")):
        fails.append("source: need paper and section or equation")
    return fails


def run_checks(spec, case, tr, label):
    """Shape (spec_schema.py, B) + behaviour (checks.py, C). Falls back to core_checks."""
    warns = []
    try:
        import spec_schema
        fails = list(spec_schema.validate(spec))
        warns = list(spec_schema.lint(spec))
    except Exception as e:
        tr.log("check", "spec_schema", "error", error=f"{type(e).__name__}: {e}"[:300])
        fails = core_checks(spec)
    tr.log("check", "validate_shape", "pass" if not fails else "fail", round=label,
           errors=fails[:15], warnings=warns[:10])
    try:
        import checks  # Person C
        extra = checks.run_all(spec) or []
        fails += [f for f in extra if f not in fails]
    except Exception as e:  # a broken checker must not kill the run
        tr.log("check", "checks_module", "error", error=f"{type(e).__name__}: {e}"[:300])
    tr.log("check", "run_all", "pass" if not fails else "fail",
           round=label, failed=len(fails), failures=fails[:20])
    spec_warnings[:] = warns  # handed to the revise prompt as optional fixes
    return fails


spec_warnings = []


# ----------------------------------------------------------------- revise
def relevant_fields(spec, fails):
    """Only send the broken fields back to the model (saves prompt tokens)."""
    keys, recognised = set(), False
    for f in fails:
        head = f.split(":", 1)[0].strip().split(".")[0].split("[")[0]
        if head in SPEC_FIELDS or head in spec:
            keys.add(head)
            recognised = True
        low = f.lower()
        if any(w in low for w in ("compute", "nan", "infinity", "throw", "test", "syntax", "expect")):
            keys.update({"compute", "tests", "controls", "explorations"})
            recognised = True
        if any(w in low for w in ("visual", "draw", "source path", "chart")):
            keys.update({"visuals", "compute"})
            recognised = True
    try:
        import spec_schema
        for k in spec_schema.error_fields(fails):
            keys.add(k)
            recognised = True
    except Exception:
        pass
    if not recognised:
        return dict(spec)  # can't tell what's broken: send everything
    keys |= {"title"}  # a little context
    return {k: spec[k] for k in keys if k in spec}


def merge_patch(spec, patch):
    """Shallow merge: the model returns whole top-level fields only."""
    changed = []
    for k, v in patch.items():
        if v is None or v == "":
            continue
        if spec.get(k) != v:
            spec[k] = v
            changed.append(k)
    return changed


# ----------------------------------------------------------------- output
def static_html_check(page):
    """Final guard on the rendered page: nothing may load from the network."""
    bad = []
    if re.search(r"<script[^>]+\bsrc\s*=\s*[\"']?\s*(https?:)?//", page, re.I):
        bad.append("external <script src>")
    if re.search(r"<link[^>]+\bhref\s*=\s*[\"']?\s*(https?:)?//", page, re.I):
        bad.append("external <link href>")
    if re.search(r"<(img|iframe|video|audio|source)[^>]+\bsrc\s*=\s*[\"']?\s*(https?:)?//", page, re.I):
        bad.append("remote media src")
    if re.search(r"(@import|url\()\s*[\"']?\s*(https?:)?//", page, re.I):
        bad.append("remote CSS import/url()")
    return bad


def basic_page(case, spec=None, note=""):
    """Last-resort page: plain text, no interactivity."""
    esc = html.escape
    spec = spec or {}
    title = esc(str(spec.get("title") or "Paper to Playground"))
    parts = [f"<h1>{title}</h1>", f"<p><b>Focus:</b> {esc(case.get('focus', ''))}</p>"]
    strip = lambda x: re.sub(r"<[^>]+>", "", str(x))
    intro = spec.get("intro")
    if isinstance(intro, dict):
        for k in ("idea", "why"):
            if intro.get(k):
                parts.append(f"<p>{esc(strip(intro[k]))}</p>")
    lims = spec.get("limitations") or []
    if isinstance(lims, list) and lims:
        parts.append("<h2>Limitations</h2><ul>" +
                     "".join(f"<li>{esc(strip(x))}</li>" for x in lims) + "</ul>")
    src = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    if src.get("paper"):
        parts.append(f"<h2>Source</h2><p>{esc(strip(src.get('paper')))} "
                     f"{esc(strip(src.get('section', '')))}</p>")
    if note:
        parts.append(f"<p style='color:#a00'>{esc(note)}</p>")
    parts.append(f"<p>Source: {esc(case.get('source_url', ''))}</p><p><i>{DISCLAIMER}</i></p>")
    return ("<!doctype html><html><head><meta charset='utf-8'><title>" + title +
            "</title><style>body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;"
            "padding:0 1rem;line-height:1.5}</style></head><body>" + "".join(parts) + "</body></html>")


def write_page(out_dir, page, tr, kind):
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    bad = static_html_check(page)
    tr.log("check", "static_html", "pass" if not bad else "fail", problems=bad)
    tr.log("render", "write_html", "ok", kind=kind, bytes=len(page.encode("utf-8")))


# ----------------------------------------------------------------- main loop
def generate(llm, case, excerpt, extra, tr):
    prompt = fill(read_text("prompts/generate.txt"),
                  focus=case["focus"], audience=case["audience"],
                  source_url=case["source_url"], excerpt=excerpt, extra=extra or "(none)")
    messages = [{"role": "user", "content": prompt}]
    spec, err = None, "not_generated"
    for attempt in (1, 2):  # one full regeneration allowed if the first reply is unusable
        res = llm.chat(messages, GEN_MAX_TOKENS, stage="generate")
        if res is None:
            if attempt == 1:
                continue
            break
        spec, err = extract_spec(res.text)
        tr.log("extract", "parse_spec", "ok" if spec else "fail", attempt=attempt,
               error=err, truncated=res.truncated,
               keys=sorted(spec.keys()) if spec else None)
        if spec:
            break
        hint = ("Your previous reply was cut off (too long). Be much more compact."
                if res.truncated else f"Your previous reply could not be parsed ({err}).")
        messages = [{"role": "user",
                     "content": prompt + "\n\nIMPORTANT: " + hint +
                     " Reply with exactly one ```json block and one ```js block, nothing else."}]
    return spec, err


def revise_loop(llm, spec, fails, case, tr, has_excerpt):
    template = read_text("prompts/revise.txt")
    rnd = 0
    while fails and rnd < MAX_REVISIONS:
        ok, why = tr.can_call(min_tokens=1500, min_seconds=40)
        if not ok:
            tr.log("revise", "skip", "budget", reason=why)
            break
        rnd += 1
        tr.revisions = rnd
        fields = relevant_fields(spec, fails)
        prompt = fill(template,
                      focus=case["focus"],
                      failures="\n".join(f"- {f}" for f in fails) + (
                          "\n\nAlso fix if easy (non-blocking):\n" +
                          "\n".join(f"- {w}" for w in spec_warnings[:5]) if spec_warnings else ""),
                      fields=json.dumps(fields, ensure_ascii=False, indent=1))
        tr.log("revise", "request_patch", "sent", revision=rnd,
               fixing=fails[:20], fields_sent=sorted(fields.keys()))
        res = llm.chat([{"role": "user", "content": prompt}], REV_MAX_TOKENS, stage="revise")
        if res is None:
            break
        patch, err = extract_patch(res.text)
        if not patch:
            tr.log("revise", "parse_patch", "fail", revision=rnd, error=err)
            continue
        before, before_fails = copy.deepcopy(spec), fails
        changed = merge_patch(spec, patch)
        normalize(spec, case, has_excerpt)
        tr.log("revise", "apply_patch", "ok" if changed else "no_change",
               revision=rnd, changed_fields=changed)
        fails = run_checks(spec, case, tr, label=f"revision_{rnd}")
        if len(fails) > len(before_fails):  # the patch made things worse: keep the old spec
            spec, fails = before, before_fails
            tr.log("revise", "rollback", "reverted", revision=rnd,
                   reason="patch increased failures")
    return spec, fails


def run(args, tr):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    tr.secret = key or None
    if not key:
        tr.log("load", "check_env", "fail", error="OPENROUTER_API_KEY is not set")
        print("error: OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2
    tr.log("load", "check_env", "ok", model=args.model)

    try:
        case = load_case(args.input, tr)
    except Exception as e:
        tr.log("load", "read_case", "fail", error=f"{type(e).__name__}: {e}"[:300])
        print(f"error: bad input: {e}", file=sys.stderr)
        return 2

    excerpt, extra, has_excerpt = build_context(case, tr)
    if os.environ.get("AGENT_DEBUG"):  # dev only: the exact excerpt text the model saw
        with open(os.path.join(args.output, "excerpt_used.txt"), "w", encoding="utf-8") as f:
            f.write(excerpt)
    llm = LLM(key, args.model, tr)

    spec, err = generate(llm, case, excerpt, extra, tr)
    if spec is None:
        write_page(args.output, basic_page(case, note="Generation failed: " + str(err)), tr, "fallback")
        tr.log("done", "finish", "failed", reason=err, exit_code=1, **tr.summary())
        return 1

    normalize(spec, case, has_excerpt)
    fails = run_checks(spec, case, tr, label="initial")
    spec, fails = revise_loop(llm, spec, fails, case, tr, has_excerpt)

    if os.environ.get("AGENT_DEBUG"):  # dev only: keep the final spec for inspection
        with open(os.path.join(args.output, "spec.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=1)

    try:
        import render  # Person B: write_html(spec, out_dir) writes out_dir/index.html
        if hasattr(render, "write_html"):
            render.write_html(spec, args.output)
            with open(os.path.join(args.output, "index.html"), encoding="utf-8") as f:
                page = f.read()
        else:
            page = render.render(spec, case)
        kind = "template"
    except Exception as e:
        tr.log("render", "template", "error", error=f"{type(e).__name__}: {e}"[:300])
        page, kind = basic_page(case, spec, note="Interactive rendering failed."), "basic"
    write_page(args.output, page, tr, kind)

    tr.log("done", "finish", "ok" if not fails else "ok_with_failures",
           remaining_failures=fails[:20], exit_code=0, **tr.summary())
    return 0


def main():
    p = argparse.ArgumentParser(description="Paper to Playground agent")
    p.add_argument("--input", required=True, help="path to case.json")
    p.add_argument("--output", required=True, help="output directory")
    p.add_argument("--model", required=True, help="OpenRouter MODEL_ID")
    args = p.parse_args()

    os.makedirs(args.output, exist_ok=True)
    tr = Tracer(os.path.join(args.output, "trace.jsonl"))
    code = 1
    try:
        code = run(args, tr)
    except Exception as e:  # safety net: always leave a trace and a page behind
        tr.log("done", "crash", "error", error=f"{type(e).__name__}: {e}"[:300],
               where=traceback.format_exc().strip().splitlines()[-3:])
        try:
            if not os.path.exists(os.path.join(args.output, "index.html")):
                case = {}
                try:
                    with open(args.input, encoding="utf-8") as f:
                        case = json.load(f)
                except Exception:
                    pass
                write_page(args.output, basic_page(case, note="The agent crashed."), tr, "fallback")
        except Exception:
            pass
        code = 1
    finally:
        tr.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
