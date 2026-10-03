"""
Mutation tests for spec_schema.py: take a valid golden spec, break one thing,
and check the validator reports it. Run from the repo root with
`python tests/test_spec_schema.py` (or pytest). Finds golden/ at the repo root.
"""
import copy
import json
import os
import sys

# Works from tests/ (repo layout) or next to spec_schema.py.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE if os.path.isdir(os.path.join(HERE, "golden")) else os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from spec_schema import validate, lint, referenced_paths, report  # noqa: E402


def _load(name):
    with open(os.path.join(ROOT, "golden", f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


GOLDEN = {name: _load(name) for name in ("entropy", "attention", "conv1d")}


def broken(name, fn):
    spec = copy.deepcopy(GOLDEN[name])
    fn(spec)
    return validate(spec, limit=None)


def expect_error(errors, *fragments):
    joined = "\n".join(errors)
    for f in fragments:
        assert f in joined, f"expected an error containing {f!r}, got:\n{joined or '(no errors)'}"


# (description, golden, mutation, fragments the error must contain)
CASES = [
    ("not an object", "entropy", None, ["must be a JSON object"]),
    ("missing title", "entropy", lambda s: s.pop("title"), ["missing required string 'title'"]),
    ("missing compute", "entropy", lambda s: s.pop("compute"), ["missing required string 'compute'"]),
    ("one control", "entropy", lambda s: s.update(controls=s["controls"][:1]), ["'controls' needs at least 2"]),
    ("duplicate id", "attention", lambda s: s["controls"][1].update(id="nk"), ["duplicate control id 'nk'"]),
    ("bad id", "entropy", lambda s: s["controls"][0].update(id="num outcomes"), ["must be a JS identifier"]),
    ("slider min>=max", "entropy", lambda s: s["controls"][0].update(min=9), ["min < max"]),
    ("default out of range", "entropy", lambda s: s["controls"][0].update(default=12), ["outside the slider range"]),
    ("toggle not bool", "attention", lambda s: s["controls"][0].update(default="yes"), ["must be true or false"]),
    ("bad select default", "conv1d", lambda s: s["controls"][4].update(default="circular"), ["not one of the select options"]),
    ("length_from unknown", "entropy", lambda s: s["controls"][1].update(length_from="k"), ["'length_from' refers to 'k'"]),
    ("length and length_from", "entropy", lambda s: s["controls"][1].update(length=4), ["exactly one of 'length' or 'length_from'"]),
    ("matrix not numeric", "attention", lambda s: s["controls"][5].update(default=[["1", 0]]), ["list of finite numbers"]),
    ("unknown type", "entropy", lambda s: s["controls"][0].update(type="knob"), ["'type' must be one of"]),
    ("compute not a function", "entropy", lambda s: s.update(compute="return {values:{}}"), ["single function expression"]),
    ("compute as const", "entropy", lambda s: s.update(compute="const compute = (s) => ({values: {}})"), ["single function expression"]),
    ("compute uses Date", "entropy", lambda s: s.update(compute=s["compute"].replace("var n", "var t = Date.now(); var n")), ["deterministic"]),
    ("compute uses setTimeout", "entropy", lambda s: s.update(compute=s["compute"].replace("var n", "setTimeout(f, 1); var n")), ["synchronous"]),
    ("slider default off grid", "entropy", lambda s: s["controls"][0].update(default=2.5), ["not on the slider grid"]),
    ("test state off grid", "attention", lambda s: s["tests"][2]["state"].update(nk=2.5), ["not on the slider grid"]),
    ("compute truncated", "entropy", lambda s: s.update(compute=s["compute"][:-40]), ["unbalanced braces"]),
    ("compute uses random", "entropy", lambda s: s.update(compute=s["compute"].replace("var n", "var r = Math.random(); var n")), ["Math.random"]),
    ("compute uses fetch", "entropy", lambda s: s.update(compute=s["compute"].replace("var n", "fetch('x'); var n")), ["network"]),
    ("only readouts", "entropy", lambda s: s.update(visuals=s["visuals"][:1]), ["at least one chart or diagram"]),
    ("bad visual type", "entropy", lambda s: s["visuals"][1].update(type="pie"), ["'type' must be one of"]),
    ("bad path root", "entropy", lambda s: s["visuals"][0]["items"][0].update(source="output.H"), ["must start with one of"]),
    ("path is a bare word", "entropy", lambda s: s["visuals"][0]["items"][0].update(source="H"), ["not a valid path"]),
    ("state path to missing control", "entropy", lambda s: s["visuals"][2]["marker"].update(x="state.m"), ["no control with id 'm'"]),
    ("bad labels", "attention", lambda s: s["visuals"][1].update(row_labels="query"), ["template containing {i}"]),
    ("line without x_source", "entropy", lambda s: s["visuals"][2].pop("x_source"), ["needs 'x_source'"]),
    ("custom without height", "conv1d", lambda s: s["visuals"][1].pop("height"), ["'height' must be"]),
    ("three explorations", "entropy", lambda s: s["explorations"].append(copy.deepcopy(s["explorations"][0])), ["exactly 2 explorations, got 3"]),
    ("no limitation", "entropy", lambda s: s["explorations"][1].update(kind="guided"), ["kind \"limitation\""]),
    ("exploration missing why", "attention", lambda s: s["explorations"][0].pop("why"), ["missing required string 'why'"]),
    ("exploration state typo", "entropy", lambda s: s["explorations"][0]["state"].update(probs=[1]), ["'probs' is not a control id"]),
    ("exploration no expect", "entropy", lambda s: s["explorations"][0].update(expect=[]), ["non-empty list"]),
    ("expect without check", "entropy", lambda s: s["tests"][0]["expect"][0].pop("equals"), ["'equals', 'min', 'max'"]),
    ("expect value not number", "entropy", lambda s: s["tests"][0]["expect"][0].update(equals="0"), ["'equals' must be a finite number"]),
    ("one test", "entropy", lambda s: s.update(tests=s["tests"][:1]), ["'tests' needs at least 2"]),
    ("test state out of range", "entropy", lambda s: s["tests"][2]["state"].update(n=16), ["outside the slider range"]),
    ("preset bad value", "conv1d", lambda s: s["presets"][0]["state"].update(mode="wrap"), ["not one of the select options"]),
    ("no limitations", "entropy", lambda s: s.update(limitations=[]), ["'limitations' needs at least 1"]),
    ("no symbols", "entropy", lambda s: s["intro"].update(symbols=[]), ["'symbols' needs at least 1"]),
    ("no source", "entropy", lambda s: s.pop("source"), ["missing required object 'source'"]),
    ("no section", "entropy", lambda s: s["source"].pop("section"), ["missing required string 'section'"]),
    ("no simplifications", "entropy", lambda s: s["source"].update(simplifications=[]), ["'simplifications' needs at least 1"]),
    ("script tag in prose", "entropy", lambda s: s["intro"].update(idea="Hi <script>alert(1)</script>"), ["forbidden markup"]),
    ("script close in compute", "entropy", lambda s: s.update(compute=s["compute"].replace("var n", "var z = '</script>'; var n")), ["forbidden markup"]),
    ("event handler", "attention", lambda s: s["explorations"][0].update(why='<b onclick="x()">click</b>'), ["forbidden markup"]),
    ("javascript url", "entropy", lambda s: s["source"].update(url="javascript:alert(1)"), ["forbidden markup"]),
    ("img tag", "entropy", lambda s: s["limitations"].append('<img src="http://x/y.png">'), ["forbidden markup"]),
    ("null field", "entropy", lambda s: s["intro"].update(idea=None), ["missing required string 'idea'"]),
    ("null control", "entropy", lambda s: s["controls"].append(None), ["controls[2]: must be an object"]),
    ("supported missing quote", "entropy", lambda s: s["source"]["supported"][0].pop("quote"), ["missing required string 'quote'"]),
]


def test_golden_specs_are_valid():
    for name, spec in GOLDEN.items():
        assert validate(spec) == [], (name, validate(spec))


def test_mutations_are_caught():
    for desc, name, fn, fragments in CASES:
        errors = validate([]) if fn is None else broken(name, fn)
        try:
            expect_error(errors, *fragments)
        except AssertionError as e:
            raise AssertionError(f"[{desc}] {e}") from None


def test_error_cap():
    spec = copy.deepcopy(GOLDEN["entropy"])
    for t in spec["tests"]:
        t["expect"] = [{"source": "nope"}, {"source": "also.bad"}]
    errors = validate(spec)
    assert len(errors) == 16 and errors[-1].startswith("... and")


def test_lint_flags_latex_and_tags():
    spec = copy.deepcopy(GOLDEN["entropy"])
    spec["intro"]["idea"] = "Entropy is $H = -\\sum p \\log p$"
    spec["intro"]["why"] = 'See <span class="x">this</span>'
    joined = "\n".join(lint(spec))
    assert "LaTeX" in joined and "<span>" in joined


def test_arrow_functions_accepted():
    for code in ["(state) => ({values: {H: 0}})", "state => ({values: {H: 0}})", "(s) => { return {values: {}}; }"]:
        spec = copy.deepcopy(GOLDEN["entropy"])
        spec["compute"] = code
        assert validate(spec) == [], (code, validate(spec))


def test_report_fields():
    spec = copy.deepcopy(GOLDEN["entropy"])
    spec["controls"][0]["max"] = 0
    spec.pop("source")
    spec["tests"][0]["state"]["probs"] = [1]
    r = report(spec)
    assert r["fields"] == ["controls", "tests", "source"], r["fields"]


def test_messages_use_json_not_python_reprs():
    spec = copy.deepcopy(GOLDEN["attention"])
    spec["controls"][0]["type"] = "knob"
    joined = "\n".join(validate(spec, limit=None))
    assert '["matrix"' in joined and "True" not in joined and "None" not in joined


def test_every_error_starts_with_a_field_name():
    from spec_schema import TOP_LEVEL
    for desc, name, fn, _ in CASES:
        if fn is None:
            continue
        for e in broken(name, fn):
            head = e.split(":")[0].split("[")[0].split(".")[0].split(" ")[0]
            assert head in TOP_LEVEL, f"[{desc}] error does not start with a spec field: {e}"


def test_referenced_paths():
    paths = dict(referenced_paths(GOLDEN["entropy"]))
    assert paths["visuals[0].items[0]"] == "values.H"
    assert paths["visuals[2].marker.x"] == "values.n"
    assert "visuals[1].x_labels" in paths


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"pass  {t.__name__}")
    print(f"\nall {len(tests)} test groups passed ({len(CASES)} mutations)")
