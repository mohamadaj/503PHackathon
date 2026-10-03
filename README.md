# Paper to Playground: agentic explainer generator

Given a research-paper case (`case.json`), the agent produces one self-contained interactive explainer page (`out/index.html`) and a trace of every step (`out/trace.jsonl`).

## Team
- Mohamad El Ajouz: agent core (CLI, OpenRouter client, trace + budget guard, excerpt compression, extraction and repair, main loop, README)
- Adam Hijazi: page template, widgets, charts, `render.py`, spec contract
- Taha Fayed: `checks.py` JS harness, `spec_schema.py` validation, test cases

## Run
```bash
python -m pip install -r requirements.txt
# Linux/macOS: export OPENROUTER_API_KEY=sk-or-...   Windows PowerShell: $env:OPENROUTER_API_KEY="sk-or-..."
python agent.py --input examples/entropy/case.json --output out --model MODEL_ID
```
Python 3.11. Dependencies: `requests`, `quickjs` (prebuilt wheels; used to run the generated JavaScript during checks).
Open `out/index.html` (or `cd out && python -m http.server`) in Chromium. The page needs no internet and no API key.

**MODEL_ID tested with:** `deepseek/deepseek-v4.1-flash` (DeepSeek V4.1 Flash via OpenRouter). Any OpenRouter chat model works; nothing is hard-coded to one model.

## Architecture
```
case.json ─► load + validate input
          ─► context: use the excerpt if present (any long text field); compress it locally
              (clean, drop duplicate sentences, keep the paragraphs most relevant to the focus,
              verbatim and in order). No network fetch: only OpenRouter is reachable.
          ─► GENERATE  (1 LLM call) understand + plan + write: one JSON spec + a JS compute()
          ─► extract   fenced blocks; repair trailing commas, smart quotes, unescaped inner quotes
          ─► CHECK     (0 tokens) local repairs → text normalization → spec_schema.validate (shape)
                       → checks.py runs compute() in QuickJS on defaults, every test, every
                       exploration, presets and fuzzed control values; resolves every chart path
          ─► REVISE    only if checks fail: send only the failures + the broken fields, merge the
                       returned fields; roll back if a patch makes things worse; stop if it changes
                       nothing; at most 2 revisions → CHECK again
          ─► verify quotes against the FULL original excerpt (drop unverifiable ones)
          ─► render the spec into the fixed template → out/index.html → static offline check
```
`trace.jsonl` records every step (stage / action / result), with the real OpenRouter token usage
and elapsed seconds of each call, every check and its failures, every revision and repair.
A budget guard stops at 8 calls / 480 s / 26k completion tokens (below the 10 / 600 s / 30k limits).
Reasoning tokens are switched off and OpenRouter is asked for its fastest provider, because tokens and
latency are scored. Typical run: 1 LLM call, about 2–3k prompt + 3–5k completion tokens, 10–25 s.

| File | Role |
|---|---|
| `agent.py` | CLI, main loop, local repairs, revision logic, exit codes |
| `llm.py` | OpenRouter client: real usage logging, retries inside the budget, reasoning off, fastest provider |
| `tracer.py` | JSONL trace + budget guard (never logs the key or model reasoning) |
| `compress.py` | zero-token excerpt compression (relevance-ranked, verbatim) |
| `extract.py` | robust JSON/JS extraction and repair from model replies |
| `prompts/` | generation and revision prompts |
| `spec_schema.py` | structural validation of the spec (shape, types, cross-references) |
| `checks.py` | runs the spec's JavaScript in QuickJS; verifies quotes; checks the final HTML |
| `render.py`, `templates/` | spec → self-contained HTML (see below) |
| `tools/run_cases.py` | dev only: runs every `examples/*/case.json` N times and writes a summary table |

Exit codes: `0` page written from a spec (even if some checks still fail; remaining failures are in the trace), `1` no usable spec (fallback page written), `2` bad input or missing key.

### Page template and rendering

The model never writes HTML, CSS or chart code. It returns one JSON **spec**
(format: [`docs/spec_contract.md`](docs/spec_contract.md)) with the text, the control definitions,
a pure JavaScript `compute(state)` function, a list of visuals, two guided explorations, tests and
source grounding. A fixed, concept-independent template turns that spec into the page.

```
spec (JSON) ──► render.py ──► out/index.html
                  │  inlines templates/page.html + style.css + runtime.js
                  └─ embeds the spec as <script type="application/json"> (escaped)
```

* **`render.py`**: `write_html(spec, out_dir)` writes one self-contained `index.html`. It needs no network,
  CDN or fonts. The spec is escaped (`<`, `>`, `&`, U+2028/9), so model text cannot break out of
  its script tag.
* **`templates/runtime.js`**: builds the page in the browser from the spec:
  * **Controls:** slider, number, toggle, select, vector and matrix. Vectors and matrices resize from
    other controls and keep their values.
  * **Visuals:** readouts, bar, line/scatter, heatmap, table, `vectors2d`, `graph`, plus `custom`
    SVG as a last resort. Charts redraw on every input change.
  * **Explorations:** each card has a *Try it* button that applies its state and shows a live ✓
    for the values the text claims.
  * **Built-in self-checks:** the spec's tests run again in the browser, using the same `compute`
    the agent's checker ran.
* **Robustness:**
  * The spec is normalized at load time, so wrong types and missing fields are dropped or coerced
    instead of crashing the page.
  * Every section and every visual fails independently, with a visible message.
  * Undefined results show the `compute` function's `warning` banner instead of NaN.
  * Model text may only use `b i em strong sub sup code br`; it is parsed in an inert
    document, so scripts never run.
* **Honest numbers:**
  * Every number on the page comes from `compute` running live.
  * Fixed axes clip out-of-range data and say so.
  * Off-scale markers are labelled as such rather than drawn at the edge.
  * A fixed disclaimer states that the demo does not reproduce the paper's results.

### Testing the template (development only)

| Command | What it does |
|---|---|
| `python render.py golden/entropy.json runs/entropy` | Render one spec to `runs/entropy/index.html` |
| `python tests/browser_check.py golden/*.json tests/coverage/*.json tests/robustness/*.json` | Render each spec, load it in headless Chrome/Chromium, report boot status, self-check results and visible errors |

* `golden/` holds hand-written reference specs (entropy, attention, 1-D convolution).
* `tests/coverage/` holds specs that exercise every visual type (gradient descent, aliasing,
  projection, Markov chain).
* `tests/robustness/` holds deliberately broken specs: syntax errors, throwing `compute`, wrong
  types and script injection.

`browser_check.py` needs a local Chrome or Chromium (set `CHROME=/path` if it is not found). It is
not used by `agent.py`, and the assessment environment does not need it.

### Reuse and credits (template)

The template, runtime and chart code were written for this project, with an AI coding assistant
(Claude Code). They use no third-party libraries, fonts or assets: charts are plain SVG built in
vanilla JavaScript, and fonts are the system font stack.

## Example
`examples/entropy/case.json` → `examples/entropy/out/index.html` + `trace.jsonl` (TODO: commit the final run).
Other practice inputs are in `examples/*/case.json`; outputs during assessment are generated afresh.

## Credits and reuse
- Libraries: [`requests`](https://pypi.org/project/requests/) (HTTP), [`quickjs`](https://pypi.org/project/quickjs/) (QuickJS engine bindings, used to run generated JavaScript in checks). No other third-party code, fonts or assets.
- Model access via [OpenRouter](https://openrouter.ai/docs/quickstart).
- AI coding assistants (Claude, Claude Code) were used during development; all code was reviewed and tested by the team.
- Papers used for practice inputs are cited in each `examples/*/case.json` (`source_url`).
