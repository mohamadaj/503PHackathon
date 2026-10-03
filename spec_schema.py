"""
spec_schema.py: structural validator for the page spec (spec contract v1).

    validate(spec)          -> list[str]   blocking errors, worded for the revision prompt.
                                           Every line starts with the top-level field it is
                                           about ("compute: ...", "visuals[2]: ...", "tests[0].state: ...").
    lint(spec)              -> list[str]   non-blocking quality warnings
    report(spec)            -> dict        {"ok", "errors", "fields", "warnings"} for trace.jsonl
    error_fields(errors)    -> list[str]   top-level fields to send to the revise prompt
    referenced_paths(spec)  -> list[(where, path)]
        every data path the spec reads, so checks.py can confirm each one
        actually exists in the compute output after running the JS.

This file checks SHAPE only: required fields, types, ranges, cross-references.
Whether `compute` runs, returns finite numbers and passes its tests is checked
by the JS harness in checks.py. Standard library only.

    python spec_schema.py golden/*.json
"""
import json
import math
import re
import sys

MAX_ERRORS = 15  # keep the revision prompt short

CONTROL_TYPES = {"slider", "number", "toggle", "select", "vector", "matrix"}
VISUAL_TYPES = {"readouts", "bar", "line", "heatmap", "table", "custom"}
CHART_TYPES = {"bar", "line", "heatmap", "custom"}
EXPLORATION_KINDS = {"guided", "limitation"}
PATH_ROOTS = ("values", "series", "matrices", "tables", "state")
ALLOWED_TAGS = {"b", "i", "em", "strong", "sub", "sup", "code", "br"}
TOP_LEVEL = {"title", "subtitle", "audience", "intro", "controls", "presets", "compute",
             "visuals", "explorations", "limitations", "tests", "source"}

FUNC_EXPR = re.compile(r"^\s*(function\b|\(?\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\)?\s*=>|\(\s*[A-Za-z_$][A-Za-z0-9_$,\s]*\)\s*=>)")
JS_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
PATH_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z0-9_$]+)+$")

# Markup that must never appear in model output (prose or code strings).
INJECTION = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|style|link|meta|base|form|img|svg|math|frame)\b"
    r"|javascript\s*:|\bon[a-z]+\s*=\s*[\"']|<!--",
    re.I)

# Patterns that break the "pure, offline" rules. (regex, explanation)
FORBIDDEN_COMPUTE = [
    (r"Math\.random|\bDate\b|performance\.now", "must be deterministic (no Math.random, Date or performance.now)"),
    (r"\bsetTimeout\b|\bsetInterval\b|\bPromise\b|\basync\b|\bawait\b", "must be synchronous (no timers, Promises or async)"),
    (r"\bfetch\s*\(|XMLHttpRequest|WebSocket", "must not use the network"),
    (r"\bdocument\.|\bwindow\.", "must not touch the DOM or globals"),
    (r"(^|[;\s])import[\s(]|\brequire\s*\(", "must not import modules"),
    (r"\beval\s*\(|\bnew\s+Function\b", "must not use eval or new Function"),
]
FORBIDDEN_DRAW = [
    (r"\bfetch\s*\(|XMLHttpRequest|WebSocket", "must not use the network"),
    (r"(^|[;\s])import[\s(]|\brequire\s*\(", "must not import modules"),
    (r"\beval\s*\(|\bnew\s+Function\b", "must not use eval or new Function"),
]


# ---------------------------------------------------------------- helpers

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_text(x):
    return isinstance(x, str) and x.strip() != ""


def _short_list(items, n=8):
    items = list(items)
    s = ", ".join(items[:n])
    return s + (", ..." if len(items) > n else "")


class _Collector:
    def __init__(self):
        self.errors = []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def text(self, obj, key, where, required=True):
        val = obj.get(key)
        if val is None:
            if required:
                self.err(where, f"missing required string '{key}'")
            return None
        if not _is_text(val):
            self.err(where, f"'{key}' must be a non-empty string")
            return None
        return val

    def lst(self, obj, key, where, min_len=0, required=True):
        val = obj.get(key)
        if val is None:
            if required:
                self.err(where, f"missing required list '{key}'")
            return []
        if not isinstance(val, list):
            self.err(where, f"'{key}' must be a list")
            return []
        if len(val) < min_len:
            self.err(where, f"'{key}' needs at least {min_len} item(s), got {len(val)}")
        return val

    def dct(self, obj, key, where, required=True):
        val = obj.get(key)
        if val is None:
            if required:
                self.err(where, f"missing required object '{key}'")
            return None
        if not isinstance(val, dict):
            self.err(where, f"'{key}' must be an object")
            return None
        return val

    def opt_num(self, obj, key, where):
        if key in obj and not _is_num(obj[key]):
            self.err(where, f"'{key}' must be a finite number")
            return None
        return obj.get(key)


# ---------------------------------------------------------------- paths and labels

def _check_path(c, where, path, controls):
    if not isinstance(path, str) or not PATH_RE.match(path):
        c.err(where, f"{json.dumps(path)} is not a valid path (examples: 'values.H', 'series.p', 'matrices.A.0.1')")
        return
    parts = path.split(".")
    if parts[0] not in PATH_ROOTS:
        c.err(where, f"path '{path}' must start with one of: " + ", ".join(r + "." for r in PATH_ROOTS))
    elif parts[0] == "state" and parts[1] not in controls:
        c.err(where, f"path '{path}' reads state.{parts[1]}, but there is no control with id '{parts[1]}'")


def _check_path_or_number(c, where, value, controls):
    if _is_num(value):
        return
    _check_path(c, where, value, controls)


def _check_labels(c, where, value, controls):
    """Labels may be an array, a template containing {i}, or a path."""
    if isinstance(value, list):
        if not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in value):
            c.err(where, "label array must contain only strings or numbers")
    elif isinstance(value, str):
        if "{i}" in value:
            return
        if value.split(".")[0] in PATH_ROOTS:
            _check_path(c, where, value, controls)
        else:
            c.err(where, f"labels {json.dumps(value)} must be an array, a template containing {{i}} (e.g. \"x{{i}}\"), or a path (e.g. \"series.labels\")")
    else:
        c.err(where, "labels must be an array, a template string, or a path")


def _check_expect(c, where, items, controls):
    if not isinstance(items, list) or not items:
        c.err(where, "'expect' must be a non-empty list of {source, equals|min|max}")
        return
    for i, e in enumerate(items):
        w = f"{where}.expect[{i}]"
        if not isinstance(e, dict):
            c.err(w, "must be an object like {\"source\": \"values.H\", \"equals\": 2, \"tol\": 1e-6}")
            continue
        if "source" not in e:
            c.err(w, "missing 'source' path")
        else:
            _check_path(c, w, e["source"], controls)
        present = [k for k in ("equals", "min", "max") if k in e]
        if not present:
            c.err(w, "needs at least one of 'equals', 'min', 'max'")
        for k in present:
            if not _is_num(e[k]):
                c.err(w, f"'{k}' must be a finite number")
        if "tol" in e and not (_is_num(e["tol"]) and e["tol"] > 0):
            c.err(w, "'tol' must be a positive number")
        if _is_num(e.get("min")) and _is_num(e.get("max")) and e["min"] > e["max"]:
            c.err(w, "'min' is greater than 'max'")


# ---------------------------------------------------------------- control values

def _check_value(c, where, ctrl, val):
    """Check one state value against its control definition."""
    t = ctrl.get("type")
    lo, hi = ctrl.get("min"), ctrl.get("max")
    if not (_is_num(lo) and _is_num(hi) and lo < hi):
        lo = hi = None  # bounds are broken or absent: report that once, not on every value
    if t in ("slider", "number"):
        if not _is_num(val):
            c.err(where, f"must be a number for {t} '{ctrl.get('id')}'")
        elif _is_num(lo) and _is_num(hi) and not (lo <= val <= hi):
            c.err(where, f"value {json.dumps(val)} is outside the {t} range [{lo}, {hi}]")
        elif t == "slider" and lo is not None and _is_num(ctrl.get("step")) and ctrl["step"] > 0:
            k = (val - lo) / ctrl["step"]
            if abs(k - round(k)) > 1e-6:
                c.err(where, f"value {json.dumps(val)} is not on the slider grid (min {lo}, step {ctrl['step']}); the browser would snap it to a different value")
    elif t == "toggle":
        if not isinstance(val, bool):
            c.err(where, "must be true or false (toggle)")
    elif t == "select":
        allowed = [o.get("value") for o in ctrl.get("options", []) if isinstance(o, dict)]
        if val not in allowed:
            c.err(where, f"{json.dumps(val)} is not one of the select options {json.dumps(allowed)}")
    elif t == "vector":
        if not isinstance(val, list) or not all(_is_num(x) for x in val):
            c.err(where, "must be a list of finite numbers (vector)")
        elif _is_num(lo) and _is_num(hi) and any(not (lo <= x <= hi) for x in val):
            c.err(where, f"vector entries must lie in [{lo}, {hi}]")
    elif t == "matrix":
        if not isinstance(val, list) or not all(isinstance(r, list) and all(_is_num(x) for x in r) for r in val):
            c.err(where, "must be a list of rows, each a list of finite numbers (matrix)")
        elif _is_num(lo) and _is_num(hi) and any(not (lo <= x <= hi) for r in val for x in r):
            c.err(where, f"matrix entries must lie in [{lo}, {hi}]")


def _check_state(c, where, state, controls):
    if not isinstance(state, dict):
        c.err(where, "'state' must be an object mapping control ids to values")
        return
    for key, val in state.items():
        ctrl = controls.get(key)
        if ctrl is None:
            c.err(f"{where}.state", f"'{key}' is not a control id (control ids: {_short_list(controls)})")
            continue
        _check_value(c, f"{where}.state.{key}", ctrl, val)


# ---------------------------------------------------------------- sections

def _all_strings(obj, where=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _all_strings(v, f"{where}.{k}" if where else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _all_strings(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        yield where, obj


def _check_injection(c, spec):
    for where, text in _all_strings(spec):
        m = INJECTION.search(text)
        if m:
            c.err(where, f"contains forbidden markup {json.dumps(m.group(0))}; prose may only use <b> <i> <em> <strong> <sub> <sup> <code> <br>, and code must not emit HTML")


def _check_intro(c, spec):
    intro = c.dct(spec, "intro", "intro")
    if intro is None:
        return
    c.text(intro, "idea", "intro")
    c.text(intro, "why", "intro")
    steps = c.lst(intro, "steps", "intro", required=False)
    for i, s in enumerate(steps):
        if not _is_text(s):
            c.err(f"intro.steps[{i}]", "must be a non-empty string")
    symbols = c.lst(intro, "symbols", "intro", min_len=1)
    for i, s in enumerate(symbols):
        w = f"intro.symbols[{i}]"
        if not isinstance(s, dict):
            c.err(w, "must be an object {symbol, meaning, unit}")
            continue
        c.text(s, "symbol", w)
        c.text(s, "meaning", w)
        if "unit" in s and not isinstance(s["unit"], str):
            c.err(w, "'unit' must be a string")


def _check_controls(c, spec):
    """Validate controls; return {id: control} for later cross-references."""
    raw = c.lst(spec, "controls", "controls", min_len=2)
    controls = {}
    for i, ctrl in enumerate(raw):
        if isinstance(ctrl, dict) and isinstance(ctrl.get("id"), str):
            if ctrl["id"] in controls:
                c.err(f"controls[{i}]", f"duplicate control id '{ctrl['id']}'")
            controls.setdefault(ctrl["id"], ctrl)

    for i, ctrl in enumerate(raw):
        w = f"controls[{i}]"
        if not isinstance(ctrl, dict):
            c.err(w, "must be an object")
            continue
        cid = ctrl.get("id")
        if not isinstance(cid, str) or not JS_IDENT.match(cid):
            c.err(w, f"'id' must be a JS identifier (letters, digits, _), got {json.dumps(cid)}")
        else:
            w = f"controls[{i}] (id '{cid}')"
        c.text(ctrl, "label", w)
        t = ctrl.get("type")
        if t not in CONTROL_TYPES:
            c.err(w, f"'type' must be one of {json.dumps(sorted(CONTROL_TYPES))}, got {json.dumps(t)}")
            continue
        if "help" in ctrl and not isinstance(ctrl["help"], str):
            c.err(w, "'help' must be a string")
        if "default" not in ctrl:
            c.err(w, "missing 'default'")

        if t == "slider":
            ok = True
            for k in ("min", "max", "step"):
                if not _is_num(ctrl.get(k)):
                    c.err(w, f"slider needs a numeric '{k}'")
                    ok = False
            if ok:
                if ctrl["min"] >= ctrl["max"]:
                    c.err(w, f"slider needs min < max (got {ctrl['min']} and {ctrl['max']})")
                if ctrl["step"] <= 0:
                    c.err(w, "slider 'step' must be positive")
        elif t == "number":
            for k in ("min", "max", "step"):
                c.opt_num(ctrl, k, w)
            if _is_num(ctrl.get("min")) and _is_num(ctrl.get("max")) and ctrl["min"] >= ctrl["max"]:
                c.err(w, "number needs min < max")
        elif t == "select":
            opts = c.lst(ctrl, "options", w, min_len=2)
            values = []
            for j, o in enumerate(opts):
                if not isinstance(o, dict) or "value" not in o or not _is_text(o.get("label")):
                    c.err(f"{w}.options[{j}]", "must be {\"value\": ..., \"label\": \"...\"}")
                    continue
                if o["value"] in values:
                    c.err(f"{w}.options[{j}]", f"duplicate option value {json.dumps(o['value'])}")
                values.append(o["value"])
        elif t == "vector":
            _check_size(c, w, ctrl, "length", controls)
            for k in ("min", "max", "step", "fill"):
                c.opt_num(ctrl, k, w)
            if "normalize" in ctrl and not isinstance(ctrl["normalize"], bool):
                c.err(w, "'normalize' must be true or false")
            if "item_labels" in ctrl:
                _check_labels(c, f"{w}.item_labels", ctrl["item_labels"], controls)
        elif t == "matrix":
            _check_size(c, w, ctrl, "rows", controls)
            _check_size(c, w, ctrl, "cols", controls)
            for k in ("min", "max", "step", "fill"):
                c.opt_num(ctrl, k, w)
            for k in ("row_labels", "col_labels"):
                if k in ctrl:
                    _check_labels(c, f"{w}.{k}", ctrl[k], controls)

        if "default" in ctrl:
            _check_value(c, f"{w}.default", ctrl, ctrl["default"])
    return controls


def _check_size(c, where, ctrl, name, controls):
    """Exactly one of `<name>` (fixed int) or `<name>_from` (control id)."""
    fixed, src = ctrl.get(name), ctrl.get(name + "_from")
    if (fixed is None) == (src is None):
        c.err(where, f"needs exactly one of '{name}' or '{name}_from'")
        return
    if fixed is not None:
        if not (isinstance(fixed, int) and not isinstance(fixed, bool) and fixed >= 1):
            c.err(where, f"'{name}' must be a positive integer")
    else:
        target = controls.get(src)
        if target is None:
            c.err(where, f"'{name}_from' refers to '{src}', which is not a control id")
        elif target.get("type") not in ("slider", "number"):
            c.err(where, f"'{name}_from' must point to a slider or number control, '{src}' is a {target.get('type')}")


def _check_presets(c, spec, controls):
    for i, p in enumerate(c.lst(spec, "presets", "presets", required=False)):
        w = f"presets[{i}]"
        if not isinstance(p, dict):
            c.err(w, "must be an object {label, state}")
            continue
        c.text(p, "label", w)
        _check_state(c, w, p.get("state"), controls)


def _check_js_function(c, where, code, forbidden):
    if not _is_text(code):
        c.err(where, "must be a non-empty string containing a JS function expression")
        return
    if not FUNC_EXPR.match(code):
        c.err(where, "must be a single function expression starting with 'function', e.g. \"function compute(state) { ... }\" (no 'const x =', no statements before it)")
    if code.count("{") != code.count("}"):
        c.err(where, "has unbalanced braces (output may be truncated)")
    for pattern, why in forbidden:
        if re.search(pattern, code):
            c.err(where, why)


def _check_visuals(c, spec, controls):
    visuals = c.lst(spec, "visuals", "visuals", min_len=1)
    types = set()
    for i, vis in enumerate(visuals):
        w = f"visuals[{i}]"
        if not isinstance(vis, dict):
            c.err(w, "must be an object")
            continue
        t = vis.get("type")
        if t not in VISUAL_TYPES:
            c.err(w, f"'type' must be one of {json.dumps(sorted(VISUAL_TYPES))}, got {json.dumps(t)}")
            continue
        types.add(t)
        w = f"visuals[{i}] ({t})"
        c.text(vis, "title", w)
        if "caption" in vis and not isinstance(vis["caption"], str):
            c.err(w, "'caption' must be a string")
        if "digits" in vis and not (isinstance(vis["digits"], int) and 0 <= vis["digits"] <= 10):
            c.err(w, "'digits' must be an integer from 0 to 10")

        if t == "readouts":
            for j, it in enumerate(c.lst(vis, "items", w, min_len=1)):
                wj = f"{w}.items[{j}]"
                if not isinstance(it, dict):
                    c.err(wj, "must be an object {source, label}")
                    continue
                _check_path(c, wj, it.get("source"), controls)
                c.text(it, "label", wj)
        elif t in ("bar", "line"):
            for j, s in enumerate(c.lst(vis, "series", w, min_len=1)):
                wj = f"{w}.series[{j}]"
                if not isinstance(s, dict):
                    c.err(wj, "must be an object {source, label}")
                    continue
                _check_path(c, wj, s.get("source"), controls)
                c.text(s, "label", wj)
            c.text(vis, "x_label", w)
            c.text(vis, "y_label", w)
            for k in ("y_min", "y_max"):
                c.opt_num(vis, k, w)
            if t == "bar" and "x_labels" in vis:
                _check_labels(c, f"{w}.x_labels", vis["x_labels"], controls)
            if t == "line":
                if "x_source" not in vis:
                    c.err(w, "line chart needs 'x_source' (path to the x values)")
                else:
                    _check_path(c, w, vis["x_source"], controls)
                m = vis.get("marker")
                if m is not None:
                    if not isinstance(m, dict) or "x" not in m:
                        c.err(f"{w}.marker", "must be an object with at least 'x'")
                    else:
                        _check_path_or_number(c, f"{w}.marker.x", m["x"], controls)
                        if "y" in m:
                            _check_path_or_number(c, f"{w}.marker.y", m["y"], controls)
        elif t in ("heatmap", "table"):
            if "source" not in vis:
                c.err(w, "missing 'source' path")
            else:
                _check_path(c, w, vis["source"], controls)
            if t == "heatmap":
                for k in ("row_labels", "col_labels"):
                    if k in vis:
                        _check_labels(c, f"{w}.{k}", vis[k], controls)
                d = vis.get("domain")
                if d is not None and not (isinstance(d, list) and len(d) == 2 and all(_is_num(x) for x in d) and d[0] < d[1]):
                    c.err(w, "'domain' must be [lo, hi] with lo < hi")
        elif t == "custom":
            if not (_is_num(vis.get("height")) and 40 <= vis["height"] <= 1200):
                c.err(w, "'height' must be a number of pixels between 40 and 1200")
            _check_js_function(c, f"{w}.draw", vis.get("draw"), FORBIDDEN_DRAW)

    if visuals and not (types & CHART_TYPES):
        c.err("visuals", "need at least one chart or diagram (bar, line, heatmap or custom), not only readouts and tables")


def _check_explorations(c, spec, controls):
    ex = spec.get("explorations")
    if not isinstance(ex, list):
        c.err("explorations", "missing required list 'explorations' (exactly 2)")
        return
    if len(ex) != 2:
        c.err("explorations", f"need exactly 2 explorations, got {len(ex)}")
    kinds = []
    for i, e in enumerate(ex):
        w = f"explorations[{i}]"
        if not isinstance(e, dict):
            c.err(w, "must be an object")
            continue
        c.text(e, "title", w)
        k = e.get("kind")
        kinds.append(k)
        if k not in EXPLORATION_KINDS:
            c.err(w, f"'kind' must be \"guided\" or \"limitation\", got {json.dumps(k)}")
        for f in ("change", "observe", "why"):
            c.text(e, f, w)
        _check_state(c, w, e.get("state"), controls)
        _check_expect(c, w, e.get("expect"), controls)
    if ex and "limitation" not in kinds:
        c.err("explorations", "at least one exploration must have kind \"limitation\" (a limitation, assumption or common misconception)")


def _check_limitations(c, spec):
    for i, s in enumerate(c.lst(spec, "limitations", "limitations", min_len=1)):
        if not _is_text(s):
            c.err(f"limitations[{i}]", "must be a non-empty string")


def _check_tests(c, spec, controls):
    for i, t in enumerate(c.lst(spec, "tests", "tests", min_len=2)):
        w = f"tests[{i}]"
        if not isinstance(t, dict):
            c.err(w, "must be an object {name, state, expect}")
            continue
        c.text(t, "name", w)
        _check_state(c, w, t.get("state", {}), controls)
        _check_expect(c, w, t.get("expect"), controls)


def _check_source(c, spec):
    src = c.dct(spec, "source", "source")
    if src is None:
        return
    for k in ("paper", "url", "section"):
        c.text(src, k, "source")
    for k in ("authors", "equation", "context_note"):
        if k in src and not isinstance(src[k], str):
            c.err("source", f"'{k}' must be a string")
    if "year" in src and not isinstance(src["year"], (str, int)):
        c.err("source", "'year' must be a string or integer")
    for i, s in enumerate(c.lst(src, "supported", "source")):
        w = f"source.supported[{i}]"
        if not isinstance(s, dict):
            c.err(w, "must be an object {claim, quote}")
            continue
        c.text(s, "claim", w)
        c.text(s, "quote", w)
    for i, s in enumerate(c.lst(src, "simplifications", "source", min_len=1)):
        if not _is_text(s):
            c.err(f"source.simplifications[{i}]", "must be a non-empty string")


# ---------------------------------------------------------------- public API

def validate(spec, limit=MAX_ERRORS):
    """Return a list of blocking errors. Empty list means the spec has the right shape."""
    if not isinstance(spec, dict):
        return [f"spec: must be a JSON object, got {type(spec).__name__}"]
    c = _Collector()
    c.text(spec, "title", "title")
    for k in ("subtitle", "audience"):
        if k in spec and not isinstance(spec[k], str):
            c.err(k, "must be a string")
    _check_intro(c, spec)
    controls = _check_controls(c, spec)
    _check_presets(c, spec, controls)
    if "compute" not in spec:
        c.err("compute", "missing required string 'compute'")
    else:
        _check_js_function(c, "compute", spec["compute"], FORBIDDEN_COMPUTE)
    _check_visuals(c, spec, controls)
    _check_explorations(c, spec, controls)
    _check_limitations(c, spec)
    _check_tests(c, spec, controls)
    _check_source(c, spec)
    _check_injection(c, spec)

    errors = c.errors
    if limit is not None and len(errors) > limit:
        extra = len(errors) - limit
        errors = errors[:limit] + [f"... and {extra} more error(s); fix the ones above first"]
    return errors


_SKIP_PROSE = {"compute", "draw", "url", "quote"}
_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)([^>]*)>")
_LATEX = re.compile(r"\$[^$\n]+\$|\\(frac|sum|sqrt|cdot|times|alpha|beta|theta|sigma|mu|log|left|right|mathbf|text)\b|\\\(|\\\[")
_MARKDOWN = re.compile(r"\*\*[^*]+\*\*|`[^`]+`|^\s*#{1,6}\s|\[[^\]]+\]\([^)]+\)", re.M)


def _walk_strings(obj, where):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in _SKIP_PROSE:
                yield from _walk_strings(v, f"{where}.{k}" if where else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{where}[{i}]")
    elif isinstance(obj, str):
        yield where, obj


def lint(spec, limit=10):
    """Non-blocking quality warnings. Worth fixing if a revision call happens anyway."""
    if not isinstance(spec, dict):
        return []
    warns = []
    unknown = sorted(set(spec) - TOP_LEVEL)
    if unknown:
        warns.append(f"spec: unknown top-level keys {json.dumps(unknown)} are ignored by the template")
    for where, s in _walk_strings(spec, ""):
        for m in _TAG.finditer(s):
            name, attrs = m.group(2).lower(), m.group(3).strip()
            if name not in ALLOWED_TAGS:
                warns.append(f"{where}: tag <{name}> is not allowed and will show as plain text (allowed: {' '.join(sorted(ALLOWED_TAGS))})")
                break
            if attrs and attrs != "/":
                warns.append(f"{where}: tags must not have attributes, found <{name} {attrs}>")
                break
        else:
            if _LATEX.search(s):
                warns.append(f"{where}: looks like LaTeX; write math with Unicode and <sub>/<sup> instead")
            elif _MARKDOWN.search(s):
                warns.append(f"{where}: looks like Markdown; use <b>, <i>, <code> instead")
    for i, vis in enumerate(spec.get("visuals") or []):
        if isinstance(vis, dict) and vis.get("type") in CHART_TYPES and not _is_text(vis.get("caption")):
            warns.append(f"visuals[{i}]: no caption; say what the learner should look at")
    src = spec.get("source")
    if isinstance(src, dict) and isinstance(src.get("supported"), list) and not src["supported"]:
        warns.append("source.supported is empty; the page will have no statements backed by the excerpt")
    if len(warns) > limit:
        warns = warns[:limit] + [f"... and {len(warns) - limit} more warning(s)"]
    return warns


def error_fields(errors):
    """Top-level spec fields the errors point at, e.g. ["controls", "tests"]."""
    out = []
    for e in errors:
        head = re.split(r"[\[.:\s(]", e, maxsplit=1)[0]
        if head in TOP_LEVEL and head not in out:
            out.append(head)
    return out


def report(spec):
    """Full result for the trace: all errors (uncapped) plus warnings."""
    errors = validate(spec, limit=None)
    return {"ok": not errors, "errors": errors, "fields": error_fields(errors), "warnings": lint(spec)}


def referenced_paths(spec):
    """Every (where, path) the page reads from compute output or state.
    checks.py should resolve each one after running compute(defaults)."""
    out = []

    def add(where, p):
        if isinstance(p, str) and "{i}" not in p and p.split(".")[0] in PATH_ROOTS:
            out.append((where, p))

    if not isinstance(spec, dict):
        return out
    for i, ctrl in enumerate(spec.get("controls") or []):
        if isinstance(ctrl, dict):
            for k in ("item_labels", "row_labels", "col_labels"):
                add(f"controls[{i}].{k}", ctrl.get(k))
    for i, vis in enumerate(spec.get("visuals") or []):
        if not isinstance(vis, dict):
            continue
        w = f"visuals[{i}]"
        for k in ("source", "x_source", "x_labels", "row_labels", "col_labels"):
            add(f"{w}.{k}", vis.get(k))
        for j, it in enumerate(vis.get("items") or []):
            if isinstance(it, dict):
                add(f"{w}.items[{j}]", it.get("source"))
        for j, s in enumerate(vis.get("series") or []):
            if isinstance(s, dict):
                add(f"{w}.series[{j}]", s.get("source"))
        m = vis.get("marker")
        if isinstance(m, dict):
            add(f"{w}.marker.x", m.get("x"))
            add(f"{w}.marker.y", m.get("y"))
    for group in ("explorations", "tests"):
        for i, e in enumerate(spec.get(group) or []):
            if isinstance(e, dict):
                for j, x in enumerate(e.get("expect") or []):
                    if isinstance(x, dict):
                        add(f"{group}[{i}].expect[{j}]", x.get("source"))
    return out


# ---------------------------------------------------------------- CLI

def main(paths):
    failed = False
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                spec = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"FAIL  {path}: cannot read JSON ({e})")
            failed = True
            continue
        r = report(spec)
        print(f"{'OK  ' if r['ok'] else 'FAIL'}  {path}  ({len(r['errors'])} errors, {len(r['warnings'])} warnings, {len(referenced_paths(spec))} paths)")
        for e in r["errors"]:
            print(f"    error: {e}")
        for w in r["warnings"]:
            print(f"    warn:  {w}")
        failed |= not r["ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python spec_schema.py spec.json [more.json ...]")
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
