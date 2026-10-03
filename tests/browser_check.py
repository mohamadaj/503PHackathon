"""Dev-only: render spec JSON files and load them in headless Chrome/Chromium.

Reports, per spec: did the page boot, did any injected script run, self-check
results, visible error messages, and how much text each section shows.

    python tests/browser_check.py golden/*.json tests/robustness/*.json
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from render import render_html  # noqa: E402

SECTIONS = ["hero", "intro", "playground", "explore", "limits", "source", "checks"]


def find_chrome() -> str:
    candidates = [
        os.environ.get("CHROME", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    candidates += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "chrome")]
    for c in candidates:
        if c and Path(c).exists():
            return c
    sys.exit("Chrome/Chromium not found; set CHROME=/path/to/chrome")


class DomScan(HTMLParser):
    """Collect visible error messages and per-section text length."""

    def __init__(self):
        super().__init__()
        self.body_attrs: dict = {}
        self.stack: list[dict] = []
        self.errors: list[str] = []
        self.section_text = {s: 0 for s in SECTIONS}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "body":
            self.body_attrs = a
        cls = (a.get("class") or "").split()
        in_noscript = any(n["tag"] == "noscript" for n in self.stack)
        is_err = ("verr" in cls or "banner" in cls or "boot-error" in cls) and "hidden" not in a and not in_noscript
        self.stack.append({"tag": tag, "err": is_err, "id": a.get("id"), "buf": []})

    def handle_endtag(self, tag):
        while self.stack:
            node = self.stack.pop()
            if node["err"]:
                text = " ".join("".join(node["buf"]).split())
                if text:
                    self.errors.append(text)
            if node["tag"] == tag:
                break

    def handle_data(self, data):
        for node in self.stack:
            if node["err"]:
                node["buf"].append(data)
            if node["id"] in self.section_text:
                self.section_text[node["id"]] += len(data.strip())


def check(spec_path: str, chrome: str, workdir: Path) -> dict:
    name = Path(spec_path).stem
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        html = render_html(spec)
    except Exception as e:  # render failure is itself a finding
        return {"name": name, "render_error": repr(e)}
    page = workdir / name / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(html, encoding="utf-8")
    proc = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--virtual-time-budget=4000", "--dump-dom", page.as_uri()],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    scan = DomScan()
    scan.feed(proc.stdout)
    b = scan.body_attrs
    return {
        "name": name,
        "booted": b.get("data-ready") == "1",
        "pwned": b.get("data-pwned"),
        "checks": b.get("data-checks"),
        "errors": scan.errors,
        "section_chars": scan.section_text,
        "html": str(page),
    }


def main(argv: list[str]) -> int:
    paths = [p for a in argv for p in sorted(glob.glob(a))] or sorted(glob.glob("golden/*.json"))
    chrome = find_chrome()
    workdir = Path(tempfile.mkdtemp(prefix="p2p_check_"))
    bad = 0
    for p in paths:
        r = check(p, chrome, workdir)
        ok = "render_error" not in r and r["booted"] and not r["pwned"]
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {r['name']}")
        for k in ("render_error", "pwned", "checks"):
            if r.get(k):
                print(f"    {k}: {r[k]}")
        if "section_chars" in r:
            empty = [s for s, n in r["section_chars"].items() if n == 0]
            print(f"    booted={r['booted']}  empty sections={empty or 'none'}")
            for e in r["errors"]:
                print(f"    ! {e[:150]}")
    print(f"\npages in {workdir}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
