"""
checks.py: run the spec's JavaScript in QuickJS and check what the page will really show.

    run_all(spec)                -> list[str]  blocking failures; [] means "ship it". Never raises.
    check_report(spec)           -> dict       same run plus stats and notes, for trace.jsonl
    verify_quotes(spec, excerpt) -> list[str]  drops source.supported items whose quote is not in the
                                               excerpt (mutates spec); returns notes for the trace
    check_html(html)             -> list[str]  final page: no resource loads, required sections present
    ENGINE_OK                    -> bool       False if the JS engine could not be imported

Every failure line starts with the top-level spec field to revise ("compute: ...",
"tests[3].expect[0]: ...", "visuals[2].series[0]: ...", "controls: ..."), like spec_schema.validate.

How the JS runs: the state rules are NOT re-implemented here. We copy the page's own functions
(defaultState, mergeState, conform, compileFn, evalExpect, ...) out of templates/runtime.js and run
them in QuickJS next to the spec's compute, so the checks see exactly what the browser sees.

    python checks.py golden/*.json
"""
import json
import math
import re
import sys
import time
import unicodedata
from pathlib import Path

import spec_schema

try:
    import quickjs
    ENGINE_OK = True
except Exception:  # noqa: BLE001  (missing wheel, broken install, ...)
    quickjs = None
    ENGINE_OK = False

RUNTIME_JS = Path(__file__).resolve().parent / "templates" / "runtime.js"

CALL_TIMEOUT_S = 1.0        # per compute()/draw() call
TOTAL_BUDGET_S = 10.0       # whole check; stop early after this
MAX_TIMEOUTS = 2            # after this many 1 s timeouts, stop running more states
MEMORY_LIMIT = 64 * 1024 * 1024
MAX_FAILURES = 15           # keep the revision prompt short (same cap as spec_schema)
MIN_LIVE_CONTROLS = 2
MIN_QUOTE_CHARS = 12        # letters/digits after normalizing; shorter quotes cannot be verified
NO_EXCERPT_NOTE = "Excerpt not provided; statements are based on the brief."
SECTION_IDS = ("intro", "playground", "explore", "limits", "source")
MAX_HTML_BYTES = 3_000_000

# Functions copied verbatim from templates/runtime.js (they close over `controls` and `byId`).
RUNTIME_FUNCS = ("isNum", "fmt", "clamp", "deepCopy", "linScale", "isObj", "asArr", "objs", "str", "strs",
                 "normalize", "resolve", "range", "dim", "toNum", "scalarDefault", "conform",
                 "defaultState", "mergeState", "compileFn", "evalExpect")


# ---------------------------------------------------------------- public API

def run_all(spec):
    """Blocking failures for this spec: validation errors, then JS failures (deduped). Never raises."""
    return check_report(spec)["failures"]


def check_report(spec):
    """Like run_all, for the trace: {"ok", "failures", "notes", "lint", "stats"}.
    Validation errors do not skip the JS: if compute is a non-empty string the guarded JS checks run anyway,
    so one revision can fix both kinds of problem. notes and lint never block and never appear in run_all()."""
    errors, js, notes, stats, lint = [], [], [], {"internal_errors": []}, []
    try:
        errors = spec_schema.validate(spec, limit=None)
    except Exception as e:  # noqa: BLE001
        stats["internal_errors"].append(f"validate: {type(e).__name__}: {e}")
    try:
        lint = spec_schema.lint(spec)
    except Exception as e:  # noqa: BLE001
        stats["internal_errors"].append(f"lint: {type(e).__name__}: {e}")
    code = spec.get("compute") if isinstance(spec, dict) else None
    if not (isinstance(code, str) and code.strip()):
        notes.append("JS checks skipped: no compute code")
    elif not ENGINE_OK:
        notes.append("JS engine not available (pip install quickjs); only structural checks ran")
    else:
        try:
            r = _JSChecks(spec).run()
            js, notes, stats = r["failures"], r["notes"], {**r["stats"], "internal_errors":
                                                             stats["internal_errors"] + r["stats"]["internal_errors"]}
        except Exception as e:  # noqa: BLE001  (setup crash: the guards inside cannot help)
            stats["internal_errors"].append(f"setup: {type(e).__name__}: {e}")
            notes.append(f"checks.py internal error, no JS checks ran: {type(e).__name__}: {e}")
    for msg in stats["internal_errors"]:
        _log(f"internal error: {msg}")
    failures = list(dict.fromkeys(errors + [f for f in js if not f.startswith("... and ")]))
    if len(failures) > MAX_FAILURES:
        failures = failures[:MAX_FAILURES] + [f"... and {len(failures) - MAX_FAILURES} more failure(s); fix the ones above first"]
    if errors:
        notes.append(f"{len(errors)} validation error(s); JS checks ran anyway")
    return {"ok": not failures, "failures": failures, "notes": notes, "lint": lint, "stats": stats}


def verify_quotes(spec, excerpt):
    """Drop source.supported items whose quote is not in the excerpt. Mutates spec; returns notes.

    Matching: strip tags -> NFKC -> lowercase -> keep only [a-z0-9] -> substring test.
    Quotes shorter than MIN_QUOTE_CHARS after normalizing are dropped (too short to mean anything).
    Pass the FULL original excerpt from case.json. With no excerpt every item is dropped and
    source.context_note explains why. A dropped quote is not a revision failure.
    """
    try:
        src = spec.get("source") if isinstance(spec, dict) else None
        if not isinstance(src, dict):
            return ["source: missing, no quotes to verify"]
        items = src.get("supported")
        items = items if isinstance(items, list) else []
        hay = _norm_quote(excerpt) if isinstance(excerpt, str) else ""
        if not hay:
            src["supported"] = []
            src["context_note"] = NO_EXCERPT_NOTE
            return [f"source.supported: no excerpt provided; dropped all {len(items)} item(s) and set context_note"]
        kept, notes = [], []
        for i, it in enumerate(items):
            quote = it.get("quote") if isinstance(it, dict) else None
            q = _norm_quote(quote) if isinstance(quote, str) else ""
            if len(q) < MIN_QUOTE_CHARS:
                notes.append(f"source.supported[{i}]: dropped, quote missing or too short to verify ({_short(quote)})")
            elif q not in hay:
                notes.append(f"source.supported[{i}]: dropped, quote not found in the excerpt ({_short(quote)})")
            else:
                kept.append(it)
        src["supported"] = kept
        notes.append(f"source.supported: {len(kept)} of {len(items)} quote(s) verified against the excerpt")
        return notes
    except Exception as e:  # noqa: BLE001
        _log(f"verify_quotes internal error: {type(e).__name__}: {e}")
        return [f"verify_quotes skipped after an internal error: {type(e).__name__}"]


# Resource loads that would break offline use. The SVG namespace URL and the paper URL shown as text
# are fine, so we never flag a bare "http".
_RESOURCE_LOADS = [
    (re.compile(r"<script\b[^>]*\bsrc\s*=", re.I), "a <script src=...> tag loads an external script"),
    (re.compile(r"<link\b", re.I), "a <link> tag loads an external resource"),
    (re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"']?\s*(?:https?:)?//", re.I), "an <img> loads a remote image"),
    (re.compile(r"url\(\s*[\"']?\s*(?:https?:)?//", re.I), "a CSS url(...) loads a remote resource"),
    (re.compile(r"@import\b", re.I), "a CSS @import loads an external stylesheet"),
    (re.compile(r"\bfetch\s*\("), "fetch( makes a network request"),
    (re.compile(r"XMLHttpRequest"), "XMLHttpRequest makes a network request"),
]
_PLACEHOLDER = re.compile(r"__SPEC_JSON__|/\*__(?:CSS|RUNTIME)__\*/|__TITLE__")


def check_html(html):
    """Static checks on the final page. [] means it is self-contained and has every section."""
    try:
        if not isinstance(html, str) or not html.strip():
            return ["html: page is empty"]
        out = []
        for pattern, why in _RESOURCE_LOADS:
            m = pattern.search(html)
            if m:
                out.append(f"html: {why}: {_short(html[m.start():m.start() + 80])}")
        for sid in SECTION_IDS:
            if not re.search(r"""\bid\s*=\s*["']?%s["'\s>]""" % re.escape(sid), html):
                out.append(f'html: missing section id="{sid}"')
        if _PLACEHOLDER.search(html):
            out.append("html: a template placeholder was not replaced")
        size = len(html.encode("utf-8"))
        if size > MAX_HTML_BYTES:
            out.append(f"html: page is {size // 1000} kB, larger than {MAX_HTML_BYTES // 1000} kB")
        return out
    except Exception as e:  # noqa: BLE001
        _log(f"check_html internal error: {type(e).__name__}: {e}")
        return []


# ---------------------------------------------------------------- the JS harness

_HARNESS = r"""
"use strict";
%(runtime)s

var __RAW = JSON.parse(%(spec_json)s);
var __visuals = Array.isArray(__RAW.visuals) ? __RAW.visuals : [];  // raw list: indexes match visuals[i] in messages
var spec = normalize(__RAW);
var controls = spec.controls;
var byId = {};
controls.forEach(function (c) { if (c && c.id) byId[c.id] = c; });

function __err(e) { return e && e.name ? e.name + ': ' + e.message : String(e); }
var __computeFn = null, __computeErr = null;
try { __computeFn = compileFn(spec.compute, 'compute()'); } catch (e) { __computeErr = __err(e); }
var __st = null, __out = null, __draws = {};

function __info() { return JSON.stringify({ compute_error: __computeErr }); }

// Non-finite numbers, found here because JSON.stringify would turn NaN into null.
function __scan(x, path, bad) {
  if (bad.length >= 5) return bad;
  if (typeof x === 'number') { if (!isFinite(x)) bad.push(path + ' = ' + String(x)); }
  else if (Array.isArray(x)) { for (var i = 0; i < x.length; i++) __scan(x[i], path + '.' + i, bad); }
  else if (x && typeof x === 'object') { Object.keys(x).forEach(function (k) { __scan(x[k], path + '.' + k, bad); }); }
  return bad;
}

// Run compute on mergeState(defaultState(), partial), exactly like the page's buttons and self-checks.
function __compute(partialJson) {
  var st = partialJson === null ? defaultState() : mergeState(defaultState(), JSON.parse(partialJson));
  __st = st; __out = null;
  var out;
  try { out = __computeFn(deepCopy(st)); } catch (e) { return JSON.stringify({ error: 'threw ' + __err(e) }); }
  if (out === null || typeof out !== 'object') return JSON.stringify({ error: 'returned ' + (out === null ? 'null' : typeof out) + ' instead of an object {values, series, matrices, tables}' });
  if (Array.isArray(out)) return JSON.stringify({ error: 'returned an array instead of an object {values, series, matrices, tables}' });
  var text;
  try { text = JSON.stringify(out); } catch (e) { return JSON.stringify({ error: 'returned a result that cannot be serialized (' + __err(e) + ')' }); }
  __out = out;
  var bad = [];
  ['values', 'series', 'matrices', 'tables'].forEach(function (k) { if (out[k] != null) __scan(out[k], k, bad); });
  return JSON.stringify({ out: text, bad: bad, warning: typeof out.warning === 'string' ? out.warning : null });
}

function __expect(listJson) {
  return JSON.stringify(JSON.parse(listJson).map(function (ex) {
    try {
      var r = evalExpect(ex, __out, __st);
      return { ok: r.ok, text: r.text, missing: resolve(ex.source, __out, __st) === undefined, available: __available(ex.source) };
    } catch (e) { return { ok: false, text: __err(e) }; }
  }));
}

// What exists at the level where a path stops resolving, e.g. "series.p, series.contrib".
function __available(path) {
  if (typeof path !== 'string') return '';
  var parts = path.split('.'), v = __out, pre = [];
  if (parts[0] === 'state') { v = __st; pre.push(parts.shift()); }
  for (var i = 0; i < parts.length; i++) {
    if (v == null || typeof v !== 'object' || v[parts[i]] === undefined) {
      var keys = v && typeof v === 'object' ? Object.keys(v) : [];
      return keys.slice(0, 12).map(function (k) { return pre.concat([k]).join('.'); }).join(', ') || '(nothing)';
    }
    v = v[parts[i]]; pre.push(parts[i]);
  }
  return '';
}

// "Defined" = something the page would print as real numbers: finite numbers, non-empty arrays of
// defined entries, tables with rows, strings/booleans. undefined, null, NaN or [] are "left out".
function __isDefined(v) {
  if (v === undefined || v === null) return false;
  if (typeof v === 'number') return isFinite(v);
  if (Array.isArray(v)) return v.length > 0 && v.every(__isDefined);
  if (typeof v === 'object') return Array.isArray(v.rows) ? v.rows.length > 0 : Object.keys(v).length > 0;
  return true;
}
function __defined(listJson) {
  return JSON.stringify(JSON.parse(listJson).map(function (p) { return __isDefined(resolve(p, __out, __st)); }));
}

function __kindOk(v, kind) {
  if (kind === 'array') return Array.isArray(v);
  if (kind === 'matrix') return Array.isArray(v) && v.length > 0;
  if (kind === 'table') return isObj(v) && Array.isArray(v.cols) && Array.isArray(v.rows);
  if (kind === 'number') return isNum(Number(v));
  return true;
}
function __describe(v) {
  if (Array.isArray(v)) return 'an array of length ' + v.length;
  if (v === null) return 'null';
  if (typeof v === 'object') return 'an object with keys ' + Object.keys(v).slice(0, 6).join(', ');
  return 'the ' + typeof v + ' ' + JSON.stringify(v);
}
function __paths(listJson) {
  var probs = [];
  JSON.parse(listJson).forEach(function (p) {
    var v = resolve(p.path, __out, __st);
    if (v === undefined) probs.push({ where: p.where, path: p.path, problem: 'missing', available: __available(p.path) });
    else if (!__kindOk(v, p.kind)) probs.push({ where: p.where, path: p.path, problem: 'kind', kind: p.kind, got: __describe(v) });
  });
  return JSON.stringify(probs);
}

// DOM stand-in for custom draw(): any property is a callable no-op that returns another stand-in,
// tree links are null and lengths are 0, so DOM calls neither crash nor loop forever.
var __NULLS = { firstChild: 1, lastChild: 1, parentNode: 1, parentElement: 1, nextSibling: 1, previousSibling: 1,
  firstElementChild: 1, lastElementChild: 1, nextElementSibling: 1, previousElementSibling: 1, ownerSVGElement: 1 };
function __stub() {
  var p = new Proxy(function () {}, {
    get: function (t, k) {
      if (k === Symbol.toPrimitive) return function (hint) { return hint === 'string' ? '' : 0; };
      if (k === 'then' || typeof k === 'symbol') return undefined;
      if (__NULLS[k]) return null;
      if (k === 'length' || k === 'childElementCount') return 0;
      if (k === 'children' || k === 'childNodes') return [];
      if (k === 'toString' || k === 'valueOf') return function () { return ''; };
      return p;
    },
    apply: function () { return p; },
    construct: function () { return p; },
    set: function () { return true; }
  });
  return p;
}
var document = __stub();

function __draw(i) {
  var v = __visuals[i];
  if (!(i in __draws)) { try { __draws[i] = compileFn(v.draw, 'draw()'); } catch (e) { __draws[i] = { compileError: __err(e) }; } }
  var f = __draws[i];
  if (f.compileError) return JSON.stringify({ error: 'could not be compiled: ' + f.compileError });
  var H = isNum(v.height) ? v.height : 300;
  var h = { el: function () { return __stub(); }, text: function () { return __stub(); },
            scale: linScale, fmt: fmt, color: function () { return '#000'; }, W: 600, H: H };
  try { f(__stub(), deepCopy(__st), __out, h); } catch (e) { return JSON.stringify({ error: 'threw ' + __err(e) }); }
  return JSON.stringify({ ok: true });
}
"""

_ENTRY_POINTS = ("__info", "__compute", "__expect", "__paths", "__draw", "__defined")


def _extract_js_functions(src, names):
    """Source text of each top-level-looking `function name(...) {...}` in runtime.js."""
    found = []
    for name in names:
        m = re.search(r"(?m)^[ \t]*function\s+" + re.escape(name) + r"\s*\(", src)
        if not m:
            raise RuntimeError(f"templates/runtime.js no longer defines function {name}(); update checks.RUNTIME_FUNCS")
        j, depth, quote = src.index("{", m.end()), 0, None
        while j < len(src):
            ch = src[j]
            if quote:
                if ch == "\\":
                    j += 1
                elif ch == quote:
                    quote = None
            elif ch == "\\":                      # escaped char in a regex literal, e.g. /\{i\}/
                j += 1
            elif ch in "'\"`":
                quote = ch
            elif src.startswith("//", j):
                j = src.find("\n", j)
                if j < 0:
                    break
                continue
            elif src.startswith("/*", j):
                j = src.find("*/", j) + 1
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    found.append(src[m.start():j + 1].strip())
                    break
            j += 1
        else:
            raise RuntimeError(f"could not find the end of function {name}() in templates/runtime.js")
    return "\n".join(found)


_RUNTIME_CACHE = {}


def _runtime_source():
    mtime = RUNTIME_JS.stat().st_mtime
    if _RUNTIME_CACHE.get("mtime") != mtime:
        _RUNTIME_CACHE["src"] = _extract_js_functions(RUNTIME_JS.read_text(encoding="utf-8"), RUNTIME_FUNCS)
        _RUNTIME_CACHE["mtime"] = mtime
    return _RUNTIME_CACHE["src"]


class _Engine:
    """One QuickJS context holding the page runtime + this spec. Rebuilt after any engine-level error."""

    def __init__(self, spec):
        spec_json = json.dumps(json.dumps(_clean(spec), ensure_ascii=False))
        self.code = _HARNESS % {"runtime": _runtime_source(), "spec_json": spec_json}
        self.calls = 0
        self._build()

    def _build(self):
        ctx = quickjs.Context()
        ctx.set_time_limit(CALL_TIMEOUT_S)
        ctx.set_memory_limit(MEMORY_LIMIT)
        ctx.eval(self.code)
        self.fns = {name: ctx.get(name) for name in _ENTRY_POINTS}
        self.ctx = ctx

    def call(self, name, *args):
        """(result dict/list, None) or (None, "timeout" | "engine error: ...")."""
        self.calls += 1
        try:
            return json.loads(self.fns[name](*args)), None
        except quickjs.JSException as e:
            msg = (str(e).splitlines() or [""])[0]
            self._build()  # an interrupted or out-of-memory context may be unusable
            if "interrupted" in msg:
                return None, "timeout"
            if not msg or msg == "null" or "Failed obtaining" in msg:
                msg = "out of memory"
            return None, f"engine error: {msg}"


# ---------------------------------------------------------------- the checks

class _JSChecks:
    def __init__(self, spec):
        self.spec = spec
        self.groups = {}  # failure text -> [where it happened]; one line per distinct problem
        self.notes = []
        self.internal = []  # crashes inside checks.py itself (see guard)
        self.t0 = time.perf_counter()
        self.timeouts = 0
        self.stopped = False
        self.stats = {"states": {}, "expects_passed": 0, "expects_total": 0, "draw_calls": 0}
        visuals = spec.get("visuals") if isinstance(spec.get("visuals"), list) else []
        self.customs = [i for i, v in enumerate(visuals) if isinstance(v, dict) and v.get("type") == "custom"]
        self.readout_keys = {it["source"].split(".")[1] for v in visuals if isinstance(v, dict) and v.get("type") == "readouts"
                             for it in (v.get("items") if isinstance(v.get("items"), list) else [])
                             if isinstance(it, dict) and isinstance(it.get("source"), str)
                             and re.match(r"values\.[^.]+", it["source"])}
        self.warned_notes = set()
        self.shown_paths = _displayed_value_paths(spec)
        self.js = _Engine(spec)

    # -- helpers
    def fail(self, msg, label=""):
        """Record a failure; the same problem at several states becomes one line '... (also ...)'."""
        labels = self.groups.setdefault(msg, [])
        if label and label not in labels:
            labels.append(label)

    @property
    def failures(self):
        out = []
        for msg, labels in self.groups.items():
            head, _, hint = msg.partition(" || ")  # hint goes after the labels
            line = f"{head} {labels[0]}" if labels else head
            if len(labels) > 1:
                more = f", +{len(labels) - 4} more" if len(labels) > 4 else ""
                line += f" (also {'; '.join(labels[1:4])}{more})"
            out.append(f"{line}. {hint}" if hint else line)
        return out

    def budget_left(self):
        if self.stopped:
            return False
        if self.timeouts >= MAX_TIMEOUTS or time.perf_counter() - self.t0 > TOTAL_BUDGET_S:
            self.stopped = True
            self.notes.append(f"stopped early after {time.perf_counter() - self.t0:.1f} s "
                              f"({self.timeouts} timeout(s)); remaining states were not run")
            return False
        return True

    def compute(self, partial, label, kind, expect_keys=(), expect_paths=()):
        """Run compute for one state; record compute failures; return the result dict or None."""
        self.stats["states"][kind] = self.stats["states"].get(kind, 0) + 1
        r, err = self.js.call("__compute", None if partial is None else json.dumps(partial))
        if err == "timeout":
            self.timeouts += 1
            self.fail(f"compute: took longer than {CALL_TIMEOUT_S:g} s (infinite loop or far too much work)", label)
            return None
        if err:
            self.fail(f"compute: {err}", label)
            return None
        if r.get("error"):
            self.fail(f"compute: {r['error']}", label)
            return None
        if r["bad"]:
            self.fail(f"compute: returned non-finite numbers ({', '.join(r['bad'][:3])}) || If a quantity is "
                      f"undefined for these inputs, leave it out of values and set 'warning'", label)
        if (r.get("warning") or "").strip():
            self.guard(f"warning check {label}", self.warning_with_numbers, r, label, expect_keys, expect_paths)
        return r

    def warning_with_numbers(self, r, label, expect_keys, expect_paths):
        """The page shows compute's warning as a banner but still prints every number it gets, so a
        warning must come with something left out. Failures, most specific first:
          1. the warning names a values key that is still shown as a number ("H is undefined", H = 0);
          2. (B's rule) every displayed path (readouts, charts, markers, this state's expects) is still
             fully defined, so nothing was left out: the page shows a made-up value next to the warning;
          3. the warning admits a substitute ("instead", "fall back", "assume", "using a uniform ...").
        Otherwise the remaining shown numbers may be real (a sum that really is 0): a note each."""
        warning = r["warning"].strip()
        quoted = json.dumps(_short(warning, 70), ensure_ascii=False)
        values = (json.loads(r["out"]).get("values") or {})
        shown = [k for k in sorted(self.readout_keys | set(expect_keys)) if _is_num(values.get(k))]
        named = [k for k in shown if _mentions(warning, k)]
        for k in named:
            self.fail(f"compute: values.{k} is shown as a number while compute reports the warning {quoted} || "
                      f"Leave {k} out of values when it is undefined, so the page shows '—' next to the warning", label)
        if named:
            return
        paths = list(dict.fromkeys(self.shown_paths + list(expect_paths)))
        defined, err = self.js.call("__defined", json.dumps(paths)) if paths else ([], None)
        if not err and defined and all(defined):
            self.fail(f"compute: returns warning {quoted} but every displayed value is still defined || "
                      f"Leave the undefined value(s) out of the output (don't substitute a made-up one)", label)
            return
        if _SUBSTITUTE.search(warning) and shown:
            self.fail(f"compute: the warning {quoted} says a substitute is used, yet values "
                      f"{', '.join(shown)} are still shown as numbers || Never substitute made-up inputs; leave "
                      f"undefined results out of values so the page shows '—'", label)
            return
        for k in shown:
            if k not in named and k not in self.warned_notes:
                self.warned_notes.add(k)
                self.notes.append(f"values.{k} = {values[k]} is shown while compute warns {quoted} ({label}); "
                                  f"fine if it is a real value")

    def draw_all(self, label):
        for i in self.customs:
            self.stats["draw_calls"] += 1
            r, err = self.js.call("__draw", i)
            if err == "timeout":
                self.timeouts += 1
                self.fail(f"visuals[{i}]: draw() took longer than {CALL_TIMEOUT_S:g} s (infinite loop?)", label)
            elif err:
                self.fail(f"visuals[{i}]: draw() failed: {err}", label)
            elif r.get("error"):
                self.fail(f"visuals[{i}]: draw() {r['error']}", label)

    def expects(self, where, items, name):
        res, err = self.js.call("__expect", json.dumps(items))
        if err:
            self.fail(f"{where}: could not evaluate expect: {err}")
            return
        for j, (ex, r) in enumerate(zip(items, res)):
            self.stats["expects_total"] += 1
            if r["ok"]:
                self.stats["expects_passed"] += 1
                continue
            hint = f"; path not found, available: {r['available']}" if r.get("missing") else ""
            self.fail(f"{where}.expect[{j}]: failed: {_short(r['text'], 160)}{hint} ({_short(name, 70)})")

    # -- the run
    def guard(self, name, fn, *args, **kwargs):
        """Run one check. If checks.py itself crashes there, record it and carry on with the others,
        so a bug in one check (or a spec that trips one) never silently skips all checks."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            msg = f"{name}: {type(e).__name__}: {e}"
            self.internal.append(msg)
            _log(f"internal error in check {msg}; that check was skipped, the others still ran")
            return None

    # -- the run: every check and every state in its own guard
    def run(self):
        if not self.guard("compile", self.check_compiles):
            return self.report()
        # 1. defaults: must work, be finite, deterministic, and provide every path the page reads
        base = self.guard("defaults", self.compute, None, "on the default inputs", "defaults")
        if base is None:
            return self.report()  # nothing else is meaningful
        self.guard("determinism", self.check_determinism, base)
        self.guard("paths", self.check_paths)
        self.guard("unused outputs", self.note_unused_outputs, base["out"])
        self.guard("draw on defaults", self.draw_all, "on the default inputs")
        # 2. tests, explorations, presets: each at its own state
        for group in ("tests", "explorations", "presets"):
            for i, item in enumerate(self.spec.get(group) or []):
                if not self.budget_left():
                    break
                self.guard(f"{group}[{i}]", self.check_item, group, i, item)
        # 3. fuzz one control at a time, and find controls that change nothing
        outputs = {}
        controls = self.spec.get("controls") if isinstance(self.spec.get("controls"), list) else []
        for c in controls:
            if not (isinstance(c, dict) and isinstance(c.get("id"), str)):
                continue  # already a validation error
            outputs[c["id"]] = {base["out"]}
            for value in self.guard(f"fuzz values for {c['id']}", _fuzz_values, c) or []:
                if not self.budget_left():
                    break
                self.guard(f"fuzz {c['id']}", self.fuzz_one, c, value, outputs)
        if not self.stopped:
            self.guard("dead controls", self.dead_controls, outputs)
        return self.report()

    def check_compiles(self):
        info, err = self.js.call("__info")
        if err or info.get("compute_error"):
            self.fail(f"compute: could not be compiled: {err or info['compute_error']}")
            return False
        return True

    def check_determinism(self, base):
        again = self.compute(None, "on the default inputs (second run)", "defaults")
        if again is not None and again["out"] != base["out"]:
            self.fail("compute: is not deterministic: two runs on the default inputs gave different results "
                      "(no randomness, time, or state kept between calls)")

    def check_paths(self):
        self.js.call("__compute", None)  # select the default state
        kinds = _path_kinds(self.spec)
        refs = [{"where": w, "path": p, "kind": kinds.get(w, "any")}
                for w, p in spec_schema.referenced_paths(self.spec)
                if not w.startswith(("tests[", "explorations["))]  # those are checked at their own states
        probs, err = self.js.call("__paths", json.dumps(refs))
        for pr in probs or []:
            if pr["problem"] == "missing":
                where = "the state" if pr["path"].startswith("state.") else "compute output"
                self.fail(f"{pr['where']}: path {pr['path']} not found in {where}; available: {pr['available']}")
            else:
                self.fail(f"{pr['where']}: {pr['path']} is {_short(pr['got'], 80)}, but this needs {_KIND_TEXT[pr['kind']]}")

    def check_item(self, group, i, item):
        if not isinstance(item, dict):
            return  # already a validation error
        label = f"for the state of {group}[{i}]"
        paths = [e["source"] for e in item.get("expect") or []
                 if isinstance(e, dict) and isinstance(e.get("source"), str) and not e["source"].startswith("state.")]
        keys = {p.split(".")[1] for p in paths if p.startswith("values.")}
        r = self.compute(item.get("state") or {}, label, group, expect_keys=keys, expect_paths=paths)
        if r is None:
            return
        if group != "presets":
            name = item.get("name") or item.get("title") or ""
            self.expects(f"{group}[{i}]", item.get("expect") or [], f"{group[:-1]} '{name}'")
        self.draw_all(label)

    def fuzz_one(self, c, value, outputs):
        label = f"when {c['id']} = {_short(json.dumps(value), 60)} (others at defaults)"
        r = self.compute({c["id"]: value}, label, "fuzz")
        if r is not None:
            outputs[c["id"]].add(r["out"])
            self.draw_all(label)

    def note_unused_outputs(self, out_json):
        """A note (never a failure) for compute output keys that no visual, expect or draw() reads."""
        out = json.loads(out_json)
        read = {tuple(p.split(".")[:2]) for _, p in spec_schema.referenced_paths(self.spec)}
        draw_code = " ".join(v["draw"] for v in self.spec.get("visuals") or []
                             if isinstance(v, dict) and isinstance(v.get("draw"), str))
        unused = [f"{root}.{key}" for root in ("values", "series", "matrices", "tables")
                  if isinstance(out.get(root), dict) for key in out[root]
                  if (root, key) not in read and not re.search(r"(?<![\w$])" + re.escape(key) + r"(?![\w$])", draw_code)]
        self.stats["unused_outputs"] = unused
        if unused:
            self.notes.append(f"compute outputs that nothing reads (no visual, test, exploration or draw): "
                              f"{', '.join(unused[:12])}")

    def dead_controls(self, outputs):
        used_directly = _controls_read_outside_compute(self.spec)
        live = [cid for cid, outs in outputs.items() if len(outs) > 1 or cid in used_directly]
        by_id = {c["id"]: c for c in self.spec.get("controls") or [] if isinstance(c, dict) and isinstance(c.get("id"), str)}
        # A control can be inert only at the defaults (e.g. LoRA's A while B = 0): retry each candidate with every
        # other control moved off its default before calling it dead.
        for cid in [c for c in outputs if c not in live]:
            if self.guard(f"dead-control retry {cid}", self.matters_with_others, by_id.get(cid), by_id):
                live.append(cid)
        dead = [cid for cid in outputs if cid not in live]
        self.stats["live_controls"], self.stats["dead_controls"] = live, dead
        if dead:
            self.notes.append(f"controls that changed nothing in any state tried (alone, and with each other "
                              f"control moved): {', '.join(dead)}")
        if len(live) < MIN_LIVE_CONTROLS:
            self.fail(f"controls: only {len(live)} control(s) change the computed output ({', '.join(live) or 'none'}); "
                      f"changing these from min to max changes nothing: {', '.join(dead)}. "
                      f"Make compute use every control (read state.<id>) or replace it with one that matters")

    def matters_with_others(self, c, by_id):
        """Does control c change the output once some other control is moved off its default?"""
        if not c:
            return False
        mine = _fuzz_values(c)
        for other in by_id.values():
            if other is c or not self.budget_left():
                continue
            for ov in _fuzz_values(other)[:1]:
                base, err = self.js.call("__compute", json.dumps({other["id"]: ov}))
                if err or not base or base.get("error"):
                    continue
                for v in mine:
                    r, err = self.js.call("__compute", json.dumps({other["id"]: ov, c["id"]: v}))
                    if not err and r and not r.get("error") and r["out"] != base["out"]:
                        return True
        return False

    def report(self):
        self.stats["calls"] = self.js.calls
        self.stats["timeouts"] = self.timeouts
        self.stats["seconds"] = round(time.perf_counter() - self.t0, 3)
        self.stats["engine"] = _engine_version()
        self.stats["internal_errors"] = list(self.internal)
        notes = self.notes + [f"checks.py internal error, this check was skipped: {x}" for x in self.internal]
        failures = self.failures
        if len(failures) > MAX_FAILURES:
            failures = failures[:MAX_FAILURES] + [f"... and {len(failures) - MAX_FAILURES} more failure(s); fix the ones above first"]
        return {"ok": not failures, "failures": failures, "notes": notes, "stats": self.stats}


_KIND_TEXT = {"array": "an array of numbers", "matrix": "a non-empty 2-D array (list of rows)",
              "table": "a table {cols: [...], rows: [[...], ...]}", "number": "a number"}


def _path_kinds(spec):
    """What each referenced path must resolve to, keyed like spec_schema.referenced_paths."""
    kinds = {}
    for i, v in enumerate(spec.get("visuals") or []):
        if not isinstance(v, dict):
            continue
        w, t = f"visuals[{i}]", v.get("type")
        if t in ("bar", "line", "scatter"):
            kinds[f"{w}.x_source"] = "array"
            for j, _ in enumerate(v.get("series") or []):
                kinds[f"{w}.series[{j}]"] = "array"
                kinds[f"{w}.series[{j}].x_source"] = "array"
                for k in spec_schema.Y_KEYS:
                    kinds[f"{w}.series[{j}].{k}"] = "array"
            for k in spec_schema.AXIS_BOUNDS:
                kinds[f"{w}.{k}"] = "number"
            for k, _ in spec_schema._markers(v):
                kinds[f"{w}.{k}.x"] = kinds[f"{w}.{k}.y"] = "number"
        elif t == "heatmap":
            kinds[f"{w}.source"] = "matrix"
        elif t == "table":
            kinds[f"{w}.source"] = "table"
        elif t == "vectors2d":
            kinds[f"{w}.range"] = "number"
            for j, _ in enumerate(v.get("items") or []):
                kinds[f"{w}.items[{j}]"] = kinds[f"{w}.items[{j}].from"] = "array"
        elif t == "graph":
            kinds[f"{w}.matrix"] = "matrix"
            kinds[f"{w}.edges"] = kinds[f"{w}.node_values"] = "array"
    return kinds


def _fuzz_values(c):
    """Values to try for one control (one at a time, others at defaults). Never all-zero vectors."""
    t, d = c.get("type"), c.get("default")
    if t == "slider":
        return [x for x in (c.get("min"), c.get("max")) if _is_num(x) and x != d]
    if t == "number":
        vals = [c[k] for k in ("min", "max") if _is_num(c.get(k)) and c[k] != d]
        step = c.get("step") if _is_num(c.get("step")) and c.get("step") > 0 else 1
        return vals or ([d + step] if _is_num(d) else [])
    if t == "toggle":
        return [not d]
    if t == "select":
        return [o["value"] for o in c.get("options") or [] if isinstance(o, dict) and o.get("value") != d]
    if t == "vector" and isinstance(d, list) and d and all(_is_num(x) for x in d):
        vals = [[_nudge(c, d[0])] + list(d[1:])]
        if len(d) > 1:
            vals.append(list(d[:-1]) + [_nudge(c, d[-1])])
        if _in_range(c, 0):  # degenerate inputs a learner can type: all zeros, a single nonzero entry
            vals.append([0] * len(d))
            one = 1 if _in_range(c, 1) else c.get("max")
            if len(d) > 1 and _is_num(one) and one != 0:
                vals.append([one] + [0] * (len(d) - 1))
        return vals
    if (t == "matrix" and isinstance(d, list) and d and all(isinstance(r, list) and r for r in d)
            and all(_is_num(x) for r in d for x in r)):
        # [0][0], one off-diagonal entry FLIPPED (a nonzero becomes 0, a zero becomes nonzero: adjacency and mask
        # matrices are often binarized, so +step changes nothing there) and the last entry
        off = (0, 1) if len(d[0]) > 1 else (1, 0)
        vals = []
        for (i, j), how in {(0, 0): "nudge", off: "flip", (len(d) - 1, len(d[-1]) - 1): "nudge"}.items():
            if i < len(d) and j < len(d[i]):
                m = [list(r) for r in d]
                x = m[i][j]
                m[i][j] = 0 if how == "flip" and x != 0 and _in_range(c, 0) else _nudge(c, x)
                vals.append(m)
        if len(d) > 1 and all(len(r) == len(d) for r in d):  # square: flip the symmetric pair (undirected graphs)
            m = [list(r) for r in d]
            for i, j in ((0, 1), (1, 0)):
                m[i][j] = 0 if m[i][j] != 0 and _in_range(c, 0) else _nudge(c, m[i][j])
            vals.append(m)
        return vals
    return []


_VALUE_WHERE = re.compile(r"^visuals\[\d+\]\.(items\[\d+\]|series\[\d+\](\.(source_y|y_source))?|source|matrix|node_values"
                          r"|markers?(\[\d+\])?\.[xy])$")


def _displayed_value_paths(spec):
    """Paths whose values the page prints or plots: readouts, chart series, heatmap/table/graph data,
    markers. Not labels, x grids, axis bounds or state.* inputs."""
    return [p for where, p in spec_schema.referenced_paths(spec)
            if _VALUE_WHERE.match(where) and not p.startswith("state.")]


# A warning that admits the shown numbers come from a made-up substitute.
_SUBSTITUTE = re.compile(r"\binstead\b|\bfall(?:s|ing)?[ -]?back\b|\bassum(?:e|ed|es|ing)\b|\bsubstitut\w*"
                         r"|\btreat(?:ed|ing)? as\b|\bdefaults? to\b|\b(?:using|showing|use) (?:a |the )?(?:uniform|default)\b",
                         re.I)


def _mentions(text, key):
    """Does the warning name this values key, e.g. 'H is undefined' or 'H max' for H_max?"""
    for form in {key, key.replace("_", " ")}:
        if re.search(r"(?<![\w])" + re.escape(form) + r"(?![\w])", text):
            return True
    return False


def _in_range(c, x):
    return (not _is_num(c.get("min")) or c["min"] <= x) and (not _is_num(c.get("max")) or x <= c["max"])


def _nudge(c, x):
    """x moved by one step (or 1), staying inside [min, max]."""
    step = c.get("step") if _is_num(c.get("step")) and c.get("step") > 0 else 1
    hi, lo = c.get("max"), c.get("min")
    if not _is_num(hi) or x + step <= hi:
        return x + step
    if not _is_num(lo) or x - step >= lo:
        return x - step
    return (lo + hi) / 2 if x != (lo + hi) / 2 else lo


def _controls_read_outside_compute(spec):
    """Control ids the page reads without compute: state.<id> paths and custom draw() code."""
    ids = {c.get("id") for c in spec.get("controls") or [] if isinstance(c, dict)}
    used = {p.split(".")[1] for _, p in spec_schema.referenced_paths(spec) if p.startswith("state.")}
    for v in spec.get("visuals") or []:
        code = v.get("draw") if isinstance(v, dict) else None
        if not isinstance(code, str):
            continue
        m = re.match(r"\s*(?:function\b[^(]*)?\(?\s*[\w$]+\s*,\s*([\w$]+)", code)  # 2nd parameter = state
        if m:
            used |= {cid for cid in ids if re.search(r"\b%s\s*(?:\.\s*%s\b|\[\s*['\"]%s['\"])" % (m.group(1), cid, cid), code)}
    return used & ids


# ---------------------------------------------------------------- small helpers

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _clean(obj):
    """NaN/Infinity -> None so the spec can be embedded as JSON."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _norm_quote(s):
    s = re.sub(r"<[^>]*>", "", s)
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _short(x, n=100):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def _engine_version():
    try:
        from importlib.metadata import version
        return f"quickjs {version('quickjs')}"
    except Exception:  # noqa: BLE001
        return "quickjs"


def _log(msg):
    print(f"checks.py: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- CLI

def main(paths):
    failed = False
    for path in paths:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        r = check_report(spec)
        s = r["stats"]
        print(f"{'OK  ' if r['ok'] else 'FAIL'}  {path}  ({len(r['failures'])} failures; "
              f"{s.get('calls', 0)} JS calls, {s.get('expects_passed', 0)}/{s.get('expects_total', 0)} expects, "
              f"{s.get('seconds', 0)} s)")
        for f in r["failures"]:
            print(f"    fail: {f}")
        for n in r["notes"]:
            print(f"    note: {n}")
        failed |= not r["ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("usage: python checks.py spec.json [more.json ...]")
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
