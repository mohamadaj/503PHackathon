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

def test_run_all_never_raises():
    assert run_all(None) and run_all([]) and run_all("text")
    original = checks._JSChecks.run
    checks._JSChecks.run = lambda self: 1 / 0
    try:
        assert run_all(golden("entropy")) == []  # internal error -> [] (reason goes to stderr)
    finally:
        checks._JSChecks.run = original


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
