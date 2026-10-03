<!-- Section for README.md (template & rendering). Paste under "Architecture". -->

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
