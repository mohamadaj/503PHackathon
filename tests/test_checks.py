"""
Tests for checks.py. Run from the repo root:  python tests/test_checks.py   (or pytest)

Golden specs must pass with no failures; robustness specs and deliberately broken copies of the
goldens must be flagged with a line that starts with the right spec field.
"""
import copy
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import checks  # noqa: E402
from checks import check_html, check_report, run_all, verify_quotes  # noqa: E402


def _load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return json.load(f)


GOLDEN = {name: _load(f"golden/{name}.json") for name in ("entropy", "attention", "conv1d")}


def golden(name):
    return copy.deepcopy(GOLDEN[name])


def expect_failure(failures, *fragments, field=None):
    joined = "\n".join(failures)
    for f in fragments:
        assert f in joined, f"expected a failure containing {f!r}, got:\n{joined or '(no failures)'}"
    if field:
        assert any(x.startswith(field) for x in failures), f"expected a failure starting with {field!r}, got:\n{joined}"


# ---------------------------------------------------------------- acceptance

def test_engine_is_available():
    assert checks.ENGINE_OK, "pip install quickjs (see requirements.txt)"


def test_runtime_functions_extracted_from_template():
    src = checks._runtime_source()
    for name in checks.RUNTIME_FUNCS:
        assert f"function {name}(" in src, name


def test_golden_specs_pass():
    for name, spec in GOLDEN.items():
        assert run_all(copy.deepcopy(spec)) == [], (name, run_all(copy.deepcopy(spec)))


def test_robustness_specs_are_flagged():
    paths = sorted(glob.glob(os.path.join(ROOT, "tests", "robustness", "*.json")))
    assert len(paths) == 5, paths
    for p in paths:
        with open(p, encoding="utf-8") as f:
            assert run_all(json.load(f)), f"{os.path.basename(p)} was not flagged"


def test_report_has_stats_for_the_trace():
    r = check_report(golden("attention"))
    json.dumps(r)
    s = r["stats"]
    assert r["ok"] and s["calls"] > 10 and s["expects_passed"] == s["expects_total"] > 0
    assert len(s["live_controls"]) >= 2 and s["states"]["tests"] == len(GOLDEN["attention"]["tests"])


# ---------------------------------------------------------------- compute failures

def test_compute_throws_for_n_equals_1():
    s = golden("entropy")
    s["compute"] = s["compute"].replace("var w =", "if (state.n === 1) throw new Error('n too small'); var w =")
    expect_failure(run_all(s), "threw Error: n too small", "n = 1", field="compute:")


def test_nan_on_all_zero_input_is_caught():
    s = golden("entropy")  # the golden leaves H out and sets 'warning'; this version divides by zero
    s["compute"] = s["compute"].replace("if (raw > 0) values.H = H;", "values.H = H / raw * raw;")
    expect_failure(run_all(s), "non-finite", "values.H = NaN", "tests[7]", field="compute:")


def test_missing_result_key():
    s = golden("entropy")
    s["visuals"][1]["series"][0]["source"] = "series.p_i"
    expect_failure(run_all(s), "visuals[1].series[0]: path series.p_i not found in compute output",
                   "available: series.p, series.contrib")


def test_wrong_shape():
    s = golden("entropy")
    s["visuals"][1]["series"][0]["source"] = "values.H"
    expect_failure(run_all(s), "visuals[1].series[0]: values.H is the number", "needs an array")
    s = golden("attention")
    s["visuals"][1]["source"] = "values.scale"
    expect_failure(run_all(s), "visuals[1].source", "2-D array")


def test_line_series_source_y_path_is_resolved():
    for key in ("source_y", "y_source"):
        s = golden("entropy")
        s["visuals"][2]["series"].append({"source": "series.ns", key: "series.logn", "label": "it", "style": "points"})
        assert run_all(copy.deepcopy(s)) == [], (key, run_all(copy.deepcopy(s)))
        s["visuals"][2]["series"][1][key] = "series.log_n"
        expect_failure(run_all(copy.deepcopy(s)), f"visuals[2].series[1].{key}: path series.log_n not found")
        s["visuals"][2]["series"][1][key] = "values.H"
        expect_failure(run_all(s), f"visuals[2].series[1].{key}: values.H is the number", "needs an array")


def test_infinite_loop_on_defaults_stops_fast():
    s = golden("entropy")
    s["compute"] = s["compute"].replace("var w =", "while (true) {} var w =")
    t = time.perf_counter()
    f = run_all(s)
    assert time.perf_counter() - t < 4, "an infinite loop must cost about one timeout, not one per state"
    assert len(f) == 1, f
    expect_failure(f, "took longer than 1 s", "on the default inputs", field="compute:")


def test_infinite_loop_for_one_input():
    s = golden("conv1d")
    s["compute"] = s["compute"].replace("var x = state.x,", "while (state.s === 3) {} var x = state.x,")
    expect_failure(run_all(s), "took longer than 1 s", "s = 3", field="compute:")


def test_failing_test_and_exploration():
    s = golden("entropy")
    s["tests"][1]["expect"][0]["equals"] = 2.5
    s["explorations"][0]["expect"][0]["equals"] = 0.5
    f = run_all(s)
    expect_failure(f, "tests[1].expect[0]: failed: values.H = 2", "explorations[0].expect[0]: failed")


def test_expect_on_missing_path_lists_available_keys():
    s = golden("entropy")
    s["tests"][0]["expect"][0]["source"] = "values.entropy"
    expect_failure(run_all(s), "tests[0].expect[0]", "path not found, available: values.H_max")


def test_non_deterministic_compute():
    s = golden("entropy")  # keeps a counter on the function object between calls
    s["compute"] = s["compute"].replace("var w =", "compute.k = (compute.k || 0) + 1; var w =").replace(
        "n: n };", "n: n, k: compute.k };")
    expect_failure(run_all(s), "not deterministic", field="compute:")


def test_compile_error_reported_once():
    s = golden("entropy")
    s["compute"] = "function compute(state) { return {values: {H: 1 +}}; }"
    f = run_all(s)
    assert len(f) == 1, f
    expect_failure(f, "could not be compiled", "SyntaxError", field="compute:")


def test_compute_returning_array():
    s = golden("entropy")
    s["compute"] = "function compute(state) { return [1, 2]; }"
    expect_failure(run_all(s), "returned an array", field="compute:")


def test_zero_vector_fuzz_catches_nan():
    s = golden("entropy")  # no guard against all-zero weights, and no test that would notice
    s["compute"] = s["compute"].replace("raw > 0 ? x / raw : 0", "x / raw")
    s["tests"] = [t for t in s["tests"] if "All-zero" not in t["name"]]
    expect_failure(run_all(s), "non-finite", "series.p.0 = NaN", "when p = [0, 0, 0, 0]", field="compute:")


# ---------------------------------------------------------------- warning + made-up value

def test_warning_naming_a_shown_value_fails():
    s = golden("entropy")  # H is undefined but still reported as 0
    s["compute"] = s["compute"].replace("if (raw > 0) values.H = H;", "values.H = raw > 0 ? H : 0;")
    expect_failure(run_all(s), "compute: values.H is shown as a number while compute reports the warning",
                   "Leave H out of values", "tests[7]")


WARNING_TEXT = ("'All weights are zero, so there is no probability distribution and H is undefined. "
                "Enter at least one positive weight.'")


def test_warning_with_every_displayed_value_defined_fails():
    s = golden("entropy")  # the real DeepSeek bug: vague warning, H silently computed from a made-up uniform p
    s["compute"] = (s["compute"]
                    .replace("raw > 0 ? x / raw : 0", "raw > 0 ? x / raw : 1 / w.length")
                    .replace("if (raw > 0) values.H = H;", "values.H = H;")
                    .replace(WARNING_TEXT, "'The weights sum to zero.'"))
    f = run_all(s)
    expect_failure(f, 'compute: returns warning "The weights sum to zero." but every displayed value is still defined',
                   "tests[7]", "when p = [0, 0, 0, 0]")
    assert not any("says a substitute" in x for x in f), "one line per problem"


def test_warning_admitting_a_substitute_fails():
    s = golden("entropy")  # H is left out, but sum_p is computed from a made-up uniform p and the warning says so
    s["compute"] = (s["compute"]
                    .replace("raw > 0 ? x / raw : 0", "raw > 0 ? x / raw : 1 / w.length")
                    .replace(WARNING_TEXT, "'All weights are zero, so a uniform distribution is used instead.'"))
    expect_failure(run_all(s), "says a substitute is used", "values H_max, sum_p")


def test_real_values_under_a_warning_are_notes():
    r = check_report(golden("entropy"))  # sum_p = 0 next to "H is undefined" is a real value
    assert r["ok"] and any("values.sum_p = 0 is shown while compute warns" in n for n in r["notes"]), r["notes"]


# ---------------------------------------------------------------- notes and lint never block

def test_unused_outputs_are_a_note():
    s = golden("entropy")  # add an output nothing reads (independent of what the golden itself returns)
    s["compute"] = s["compute"].replace("var values = {", "var values = { never_read: 7,")
    r = check_report(s)
    unused = r["stats"]["unused_outputs"]
    assert r["ok"] and "values.never_read" in unused and "values.H" not in unused, unused
    assert any("values.never_read" in n for n in r["notes"])
    s = golden("conv1d")  # a key read only inside draw() counts as read
    s["compute"] = s["compute"].replace("var values = {", "var values = { extra_k: k,")
    assert "values.extra_k" in check_report(s)["stats"]["unused_outputs"]
    s["visuals"][1]["draw"] = s["visuals"][1]["draw"].replace("var k = w.length,", "var kk = out.values.extra_k; var k = w.length,")
    assert "values.extra_k" not in check_report(s)["stats"]["unused_outputs"]


def test_lint_and_notes_only_in_check_report():
    s = golden("entropy")
    s["intro"]["why"] = "weights p_(i) matter"
    assert run_all(copy.deepcopy(s)) == []
    r = check_report(s)
    assert r["ok"] and any("ASCII math" in w for w in r["lint"]) and r["notes"]


def test_expect_equals_string_uses_runtime_evalexpect():
    s = golden("entropy")  # a status label in values, matched exactly (no tol) by B's evalExpect
    s["compute"] = s["compute"].replace("var values = {", "var values = { status: raw > 0 ? 'ok' : 'empty', certain: H === 0,")
    s["tests"][0]["expect"] = [{"source": "values.status", "equals": "ok"}, {"source": "values.certain", "equals": True}]
    s["tests"][7]["expect"] = [{"source": "values.status", "equals": "empty", "tol": 0.5}]
    assert run_all(copy.deepcopy(s)) == [], run_all(copy.deepcopy(s))
    s["tests"][0]["expect"][0]["equals"] = "OK"   # exact match: case matters
    s["tests"][0]["expect"][1]["equals"] = False
    f = run_all(s)
    expect_failure(f, "tests[0].expect[0]: failed: values.status = ok", "tests[0].expect[1]: failed")


def test_validation_errors_do_not_skip_js_checks():
    s = golden("entropy")
    s["source"].pop("section")                          # a validation error ...
    s["compute"] = s["compute"].replace("var w =", "if (state.n === 1) throw new Error('n too small'); var w =")
    s["visuals"].append("not a visual")                 # ... and junk the checker must survive
    r = check_report(s)
    f = r["failures"]
    expect_failure(f, "source: missing required string 'section'", "visuals[4]: must be an object")
    expect_failure(f, "compute: threw Error: n too small")   # ... JS checks still ran
    assert f.index("source: missing required string 'section'") < len(f) - 1  # validation errors come first
    assert r["stats"]["internal_errors"] == [], r["stats"]["internal_errors"]
    assert len(f) == len(set(f)) and run_all(s) == f


# ---------------------------------------------------------------- controls

def test_dead_controls():
    s = golden("entropy")  # constant output: neither n nor p changes anything
    s["compute"] = ("function compute(state) { return {values: {H: 1, H_max: 2, sum_p: 1, n: 4}, "
                    "series: {p: [1], contrib: [0], labels: ['a'], ns: [1], logn: [0]}, "
                    "tables: {contrib: {cols: ['a'], rows: [[1]]}}}; }")
    expect_failure(run_all(s), "controls: only 0 control(s) change", field="controls:")


def test_one_dead_control_is_a_note_not_a_failure():
    s = golden("entropy")
    s["controls"].append({"id": "show", "type": "toggle", "label": "Show extra", "default": False})
    r = check_report(s)
    assert r["ok"], r["failures"]
    assert r["stats"]["dead_controls"] == ["show"] and any("show" in n for n in r["notes"])


def _mini_spec(controls, compute):
    """Smallest valid spec around the given controls and compute (returns values.y and series.v)."""
    exp = [{"source": "values.y", "min": -1e9}]
    return {"title": "t", "intro": {"idea": "i", "why": "w", "symbols": [{"symbol": "y", "meaning": "m"}]},
            "controls": controls, "compute": compute,
            "visuals": [{"type": "bar", "title": "v", "x_label": "i", "y_label": "v", "series": [{"source": "series.v", "label": "v"}]},
                        {"type": "readouts", "title": "y", "items": [{"source": "values.y", "label": "y"}]}],
            "explorations": [{"title": "a", "kind": "guided", "state": {}, "change": "c", "observe": "o", "why": "w", "expect": exp},
                             {"title": "b", "kind": "limitation", "state": {}, "change": "c", "observe": "o", "why": "w", "expect": exp}],
            "limitations": ["l"], "tests": [{"name": "a", "state": {}, "expect": exp}, {"name": "b", "state": {}, "expect": exp}],
            "source": {"paper": "p", "section": "s", "supported": [], "simplifications": ["s"]}}


def test_control_that_matters_only_with_another_is_live():
    """Like LoRA: ΔW = B·A with B = 0 by default, so A alone changes nothing at the defaults."""
    s = _mini_spec([{"id": "b", "type": "slider", "label": "b", "min": 0, "max": 2, "step": 1, "default": 0},
                    {"id": "a", "type": "vector", "label": "a", "length": 3, "default": [1, 2, 3]},
                    {"id": "unused", "type": "toggle", "label": "u", "default": False}],
                   "function compute(s) { var v = s.a.map(function (x) { return s.b * x; }); "
                   "return {values: {y: v[0]}, series: {v: v}}; }")
    r = check_report(s)
    assert r["ok"], r["failures"]
    assert set(r["stats"]["live_controls"]) == {"a", "b"} and r["stats"]["dead_controls"] == ["unused"], r["stats"]


def test_binarized_symmetric_adjacency_is_live():
    """Like GCN: compute ignores the diagonal, maps any nonzero to 1 and symmetrizes A[i][j] || A[j][i]."""
    s = _mini_spec([{"id": "n", "type": "slider", "label": "n", "min": 2, "max": 4, "step": 1, "default": 3},
                    {"id": "A", "type": "matrix", "label": "A", "rows_from": "n", "cols_from": "n", "step": 1,
                     "default": [[0, 1, 0], [1, 0, 1], [0, 1, 0]]}],
                   "function compute(s) { var n = s.A.length, deg = []; for (var i = 0; i < n; i++) { var d = 0; "
                   "for (var j = 0; j < n; j++) { if (i !== j && (s.A[i][j] || s.A[j][i])) d += 1; } deg.push(d); } "
                   "return {values: {y: deg[0]}, series: {v: deg}}; }")
    r = check_report(s)
    assert r["ok"] and r["stats"]["dead_controls"] == [], (r["failures"], r["stats"])


def test_control_used_only_by_draw_is_live():
    s = golden("conv1d")  # conv1d's draw reads state.s directly
    used = checks._controls_read_outside_compute(s)
    assert "s" in used and "flip" not in used


# ---------------------------------------------------------------- custom draw

def test_draw_throws_for_one_input():
    s = golden("conv1d")
    s["visuals"][1]["draw"] = s["visuals"][1]["draw"].replace(
        "var k = w.length,", "if (state.k === 5) throw new RangeError('kernel too long'); var k = w.length,")
    f = run_all(s)
    expect_failure(f, "visuals[1]: draw() threw RangeError: kernel too long", "k = 5", "(also ")
    assert len(f) == 1, f"the same problem at 3 states must be one line: {f}"


def test_draw_infinite_loop():
    s = golden("conv1d")
    s["visuals"][1]["draw"] = s["visuals"][1]["draw"].replace("var k = w.length,", "for (;;) {} var k = w.length,")
    expect_failure(run_all(s), "visuals[1]: draw() took longer than 1 s")


def test_draw_may_use_dom_calls():
    s = golden("conv1d")
    s["visuals"][1]["draw"] = s["visuals"][1]["draw"].replace(
        "var k = w.length,",
        "var g = document.createElementNS('ns', 'g'); g.setAttribute('x', 1); svg.appendChild(g);"
        " while (svg.firstChild) svg.removeChild(svg.firstChild); var k = w.length,")
    assert run_all(s) == [], run_all(s)


# ---------------------------------------------------------------- robustness of run_all itself

def _patched(obj, name, replacement):
    """Context manager: temporarily replace obj.name and capture stderr (deliberate crashes log there)."""
    import contextlib
    import io

    @contextlib.contextmanager
    def cm():
        original, err = getattr(obj, name), io.StringIO()
        setattr(obj, name, replacement)
        try:
            with contextlib.redirect_stderr(err):
                yield err
        finally:
            setattr(obj, name, original)
    return cm()


def _boom(*args, **kwargs):
    raise ZeroDivisionError("deliberate crash injected by the test")


def test_run_all_never_raises():
    assert run_all(None) and run_all([]) and run_all("text")
    with _patched(checks._Engine, "__init__", _boom) as err:  # the JS harness cannot even be built
        assert run_all(golden("entropy")) == []
        r = check_report(golden("entropy"))
    assert "setup: ZeroDivisionError" in err.getvalue() and r["stats"]["internal_errors"]


def test_one_crashing_check_does_not_skip_the_others():
    s = golden("entropy")
    s["compute"] = s["compute"].replace("var w =", "if (state.n === 1) throw new Error('n too small'); var w =")
    with _patched(checks._JSChecks, "note_unused_outputs", _boom), _patched(checks._JSChecks, "expects", _boom) as err:
        f = run_all(copy.deepcopy(s))
        r = check_report(s)
    expect_failure(f, "threw Error: n too small", "n = 1", field="compute:")  # fuzzing still ran
    internal = r["stats"]["internal_errors"]
    assert any(x.startswith("unused outputs:") for x in internal) and any(x.startswith("tests[") for x in internal), internal
    assert any("internal error" in n for n in r["notes"]) and "deliberate crash" in err.getvalue()


def test_no_internal_errors_on_golden_and_coverage_specs():
    paths = sorted(glob.glob(os.path.join(ROOT, "golden", "*.json")) + glob.glob(os.path.join(ROOT, "tests", "coverage", "*.json")))
    assert len(paths) >= 7, paths
    for p in paths:
        r = check_report(_load(os.path.relpath(p, ROOT)))
        name = os.path.basename(p)
        assert r["stats"].get("internal_errors") == [], (name, r["stats"].get("internal_errors"))
        assert r["failures"] == [], (name, r["failures"])


def test_no_internal_errors_on_malformed_robustness_specs():
    """JS checks now run even when validation fails, so junk input must be handled, not crash a check."""
    for p in sorted(glob.glob(os.path.join(ROOT, "tests", "robustness", "*.json"))):
        r = check_report(_load(os.path.relpath(p, ROOT)))
        assert r["failures"] and r["stats"]["internal_errors"] == [], (os.path.basename(p), r["stats"]["internal_errors"])


# ---------------------------------------------------------------- verify_quotes

EXCERPT = ("The quantity H = −Σ p_i log p_i plays a central role in information theory as a measure of "
           "information, choice and uncertainty. H is zero if and only if all the p_i but one are zero; "
           "this ﬁnal case corresponds to certainty.")


def _spec_with_quotes(*quotes):
    s = golden("entropy")
    s["source"]["supported"] = [{"claim": f"claim {i}", "quote": q} for i, q in enumerate(quotes)]
    return s


def test_quotes_verified_with_normalization():
    s = _spec_with_quotes(
        "plays a central role in information theory",                      # exact
        "H is ZERO if and only if all the <i>p<sub>i</sub></i> but one are zero",  # case + tags
        "this final case corresponds to certainty",                       # ligature in the excerpt
        "entropy is the average surprise",                                # not in the excerpt
        "H = −Σ p")                                                       # too short after normalizing
    notes = verify_quotes(s, EXCERPT)
    kept = [x["claim"] for x in s["source"]["supported"]]
    assert kept == ["claim 0", "claim 1", "claim 2"], (kept, notes)
    joined = "\n".join(notes)
    assert "supported[3]: dropped, quote not found" in joined and "supported[4]: dropped" in joined
    assert "3 of 5" in joined


def test_no_excerpt_drops_all_quotes():
    s = _spec_with_quotes("plays a central role in information theory")
    notes = verify_quotes(s, None)
    assert s["source"]["supported"] == [] and s["source"]["context_note"] == checks.NO_EXCERPT_NOTE
    assert notes and "no excerpt" in notes[0]
    s = _spec_with_quotes("plays a central role in information theory")
    verify_quotes(s, "   ")
    assert s["source"]["supported"] == []


def test_verify_quotes_never_raises():
    assert verify_quotes(None, EXCERPT)
    assert verify_quotes({"source": {"supported": "oops"}}, EXCERPT)
    assert verify_quotes({"source": {"supported": [None, 3, {"quote": 5}]}}, EXCERPT)


# ---------------------------------------------------------------- check_html

def _rendered(name="entropy"):
    from render import render_html
    return render_html(golden(name))


def test_rendered_golden_pages_pass():
    for name in GOLDEN:
        html = _rendered(name)
        assert "http://www.w3.org/2000/svg" in html  # allowed: SVG namespace
        assert check_html(html) == [], (name, check_html(html))


def test_html_resource_loads_flagged():
    base = _rendered()
    bad = {
        '<script src="https://cdn.example.com/d3.js"></script>': "<script src",
        '<link rel="stylesheet" href="https://fonts.example.com/x.css">': "<link>",
        '<img src="http://example.com/a.png">': "remote image",
        "<style>body{background:url(https://example.com/bg.png)}</style>": "url(",
        "<style>@import 'x.css';</style>": "@import",
    }
    for snippet, fragment in bad.items():
        f = check_html(base.replace("</body>", snippet + "</body>"))
        assert any(fragment in x for x in f), (snippet, f)


def test_html_missing_section_and_placeholder():
    html = _rendered().replace('id="limits"', 'id="limitz"')
    assert any('missing section id="limits"' in x for x in check_html(html))
    assert any("placeholder" in x for x in check_html("<p id=intro id=playground id=explore id=limits id=source>__SPEC_JSON__"))
    assert check_html("") == ["html: page is empty"]


if __name__ == "__main__":
    tests = [(k, v) for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    failed = 0
    t0 = time.perf_counter()
    for name, fn in tests:
        try:
            fn()
            print(f"pass  {name}")
        except Exception as e:  # noqa: BLE001  (report every failure, keep going)
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed in {time.perf_counter() - t0:.1f} s")
    sys.exit(1 if failed else 0)
