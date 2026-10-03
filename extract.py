"""Pull the JSON spec and the JavaScript out of a messy model reply (Person A).

Expected reply shape (defined in prompts/generate.txt):
    ```json
    { ...spec... }
    ```
    ```js
    function compute(state) { ... }
    ```
Models add chatter, forget fences, truncate, or use smart quotes, so every
step has a fallback. Nothing here raises: failures come back as an error string,
which the main loop treats like a failed check (-> revision).
"""
import json
import re

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)(?:```|\Z)", re.S)
_JS_LANGS = {"js", "javascript", "jsx", "ecmascript"}


def fenced_blocks(text):
    """List of (lang, body). An unterminated last fence (truncation) is still returned."""
    return [(m.group(1).lower(), m.group(2).strip()) for m in _FENCE.finditer(text or "")]


def _balanced_objects(text):
    """Yield every top-level {...} substring, respecting strings/escapes."""
    i, n = 0, len(text)
    while i < n:
        start = text.find("{", i)
        if start < 0:
            return
        depth, in_str, esc = 0, False, False
        for j in range(start, n):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[start:j + 1]
                    i = j + 1
                    break
        else:
            return  # unbalanced -> truncated


def loads_lenient(s):
    """json.loads with a few common repairs. Returns (obj, error)."""
    s = (s or "").strip().lstrip("﻿")
    try:
        return json.loads(s), None
    except json.JSONDecodeError as e:
        first_err = f"{e.msg} at line {e.lineno} col {e.colno}"
    fixed = (s.replace("“", '"').replace("”", '"')
              .replace("‘", "'").replace("’", "'"))
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)                # trailing commas
    fixed = re.sub(r"\bNaN\b|\b-?Infinity\b", "null", fixed)    # non-JSON numbers
    try:
        return json.loads(fixed), None
    except json.JSONDecodeError:
        pass
    obj = _escape_inner_quotes(fixed)
    return (obj, None) if obj is not None else (None, first_err)


def _escape_inner_quotes(s, max_fixes=60):
    """Models often write text like  "change": "Press "Try it" now"  (unescaped quotes).
    json then fails with "Expecting ',' delimiter" (or ':' / '}') right after the stray quote.
    Escape the quote just before the error position and retry, a bounded number of times."""
    for _ in range(max_fixes):
        try:
            return json.loads(s)
        except json.JSONDecodeError as e:
            if not any(k in e.msg for k in ("delimiter", "Expecting property name", "Extra data")):
                return None
            q = s.rfind('"', 0, e.pos)
            if q <= 0 or s[q - 1] == "\\":
                return None
            s = s[:q] + '\\"' + s[q + 1:]
    return None


def _parse_first_object(candidates):
    last_err = "no JSON object found"
    for c in candidates:
        obj, err = loads_lenient(c)
        if isinstance(obj, dict):
            return obj, None
        last_err = err or "JSON is not an object"
    return None, last_err


def _split(text):
    blocks = fenced_blocks(text)
    js = [b for lang, b in blocks if lang in _JS_LANGS]
    json_cands = [b for lang, b in blocks if lang in ("json", "") and b.lstrip().startswith("{")]
    return blocks, js, json_cands


def _attach_js(obj, js_blocks):
    """Put fenced JS into the spec (contract v1 field names).

    - a block defining `function compute` -> spec["compute"]
    - blocks defining `function draw`     -> the `draw` of custom visuals, in order
    A fenced block beats JS embedded in the JSON string (models break escaping there).
    """
    if "compute_js" in obj and "compute" not in obj:  # legacy name
        obj["compute"] = obj.pop("compute_js")
    if not js_blocks:
        return
    draws, computes = [], []
    for b in js_blocks:
        (draws if re.search(r"function\s+draw\s*\(", b) else computes).append(b)
    if computes:
        obj["compute"] = "\n\n".join(computes).strip()
    if draws:
        customs = [v for v in obj.get("visuals", []) if isinstance(v, dict) and v.get("type") == "custom"]
        for v, d in zip(customs, draws):
            v["draw"] = d.strip()


def extract_spec(text):
    """Return (spec_dict, None) or (None, 'json_parse_error: ...')."""
    if not text or not text.strip():
        return None, "empty_reply"
    _, js, cands = _split(text)
    spec, err = _parse_first_object(cands)
    if spec is None:
        # No usable fenced JSON: strip JS blocks then scan the raw text for {...}.
        stripped = _FENCE.sub(lambda m: "" if m.group(1).lower() in _JS_LANGS else m.group(2), text)
        spec, err2 = _parse_first_object(list(_balanced_objects(stripped)))
        if spec is None:
            return None, f"json_parse_error: {err2 or err}"
    if isinstance(spec.get("spec"), dict) and len(spec) == 1:  # model wrapped it
        spec = spec["spec"]
    _attach_js(spec, js)
    return spec, None


def extract_patch(text):
    """Same as extract_spec but a JS-only reply is a valid patch (fixes compute only)."""
    if not text or not text.strip():
        return None, "empty_reply"
    _, js, cands = _split(text)
    patch, err = _parse_first_object(cands)
    if patch is None:
        stripped = _FENCE.sub(lambda m: "" if m.group(1).lower() in _JS_LANGS else m.group(2), text)
        patch, _ = _parse_first_object(list(_balanced_objects(stripped)))
    if patch is None:
        patch = {}
    if isinstance(patch.get("patch"), dict) and len(patch) == 1:
        patch = patch["patch"]
    _attach_js(patch, js)
    if not patch:
        return None, f"patch_parse_error: {err}"
    return patch, None
