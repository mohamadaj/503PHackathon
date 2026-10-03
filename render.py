"""Inline the generic template and a spec dict into one self-contained HTML page."""
from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _clean(obj):
    """Replace NaN/Infinity (invalid JSON) with None, recursively."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def spec_to_script_json(spec: dict) -> str:
    """JSON that is safe inside <script type="application/json"> (no </script>, no U+2028/9)."""
    text = json.dumps(_clean(spec), ensure_ascii=False, allow_nan=False)
    return (text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_html(spec: dict) -> str:
    page = (TEMPLATE_DIR / "page.html").read_text(encoding="utf-8")
    css = (TEMPLATE_DIR / "style.css").read_text(encoding="utf-8")
    js = (TEMPLATE_DIR / "runtime.js").read_text(encoding="utf-8")
    if "</script" in js.lower() or "</style" in css.lower():
        raise ValueError("template asset contains a closing tag that would break inlining")

    title = re.sub(r"<[^>]*>", "", str(spec.get("title") or "Interactive explanation"))
    subs = {
        "__TITLE__": html.escape(title),
        "/*__CSS__*/": css,
        "/*__RUNTIME__*/": js,
        "__SPEC_JSON__": spec_to_script_json(spec),
    }
    # Single pass so inserted content is never re-scanned for placeholders.
    pattern = re.compile("|".join(re.escape(k) for k in subs))
    return pattern.sub(lambda m: subs[m.group(0)], page)


def write_html(spec: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(render_html(spec), encoding="utf-8")
    return path


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        sys.exit("usage: python render.py SPEC.json OUT_DIR")
    spec_ = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(write_html(spec_, sys.argv[2]))
