# Spec contract (v1)

The LLM produces **one JSON object** (the *spec*). `render.py` embeds it into the generic
template; `templates/runtime.js` builds the page from it in the browser. The checker
(`checks.py`) runs the same `compute` string in a JS engine. Nothing paper-specific lives in
the template.

Golden examples: [`golden/entropy.json`](../golden/entropy.json), [`golden/attention.json`](../golden/attention.json).

## Rich text

Every prose field may contain only these tags: `<b> <i> <em> <strong> <sub> <sup> <code> <br>`
(no attributes). Everything else is shown as plain text. Write math with Unicode plus sub/sup,
e.g. `H = −Σ p<sub>i</sub> log<sub>2</sub> p<sub>i</sub>`. No LaTeX, no Markdown.

## Top level

| field | type | required | notes |
|---|---|---|---|
| `title` | string | yes | short concept name |
| `subtitle` | rich | no | one-line hook |
| `audience` | string | no | shown as a chip |
| `intro` | object | yes | see below |
| `controls` | array | yes, ≥ 2 | see below |
| `presets` | array | no | `{label, state}` buttons above the controls |
| `compute` | string | yes | JS function expression, see below |
| `visuals` | array | yes, ≥ 1 | see below |
| `explorations` | array | yes, exactly 2 | at least one with `kind: "limitation"` |
| `limitations` | array of rich | yes, ≥ 1 | assumptions / misconceptions box |
| `tests` | array | yes, ≥ 2 | run by checks.py **and** live in the page |
| `source` | object | yes | grounding, see below |

### `intro`
```json
{ "idea": "rich paragraph", "why": "rich paragraph",
  "steps": ["rich", "..."],                       // optional: how the mechanism works, in order
  "symbols": [{"symbol": "p<sub>i</sub>", "meaning": "probability of outcome i", "unit": ""}] }
```

### `controls`
Every control has `id` (JS identifier), `type`, `label`, optional `help` (rich).
`state[id]` holds its current value.

| type | extra fields | value in `state` |
|---|---|---|
| `slider` | `min max step default` | number |
| `number` | `min? max? step? default` | number |
| `toggle` | `default` (bool) | boolean |
| `select` | `options: [{value, label}]`, `default` | the option's value |
| `vector` | `default: [..]`, `length` **or** `length_from: "<control id>"`, `min? max? step?`, `fill` (default 0), `item_labels?`, `normalize?` (bool → "Normalize" button) | number[] |
| `matrix` | `default: [[..]]`, `rows`/`rows_from`, `cols`/`cols_from`, `min? max? step?`, `fill`, `row_labels?`, `col_labels?` | number[][] |

When a `*_from` control changes, the vector/matrix is resized and existing values are kept;
new cells take `default[i]` if present, else `fill`.

Labels (`item_labels`, `row_labels`, `col_labels`, `x_labels`) may be an array, a template
string containing `{i}` (1-based, e.g. `"x{i}"`), or a path (see below).

### `compute`
A **single JS function expression** as a string, e.g.
`"function compute(state) { ...; return {values:{...}, series:{...}, matrices:{...}, tables:{...}}; }"`

* Pure and deterministic: no DOM, no `Math.random`, no network, no globals. Plain ES2017.
* Must handle edge cases itself (zeros, empty, n = 1). Never return NaN for valid inputs.
* Receives a deep copy of `state`. Harness evaluates it as `(<compute string>)`.

Return shape (all keys optional):
```json
{ "values":   {"H": 1.75, "sum_p": 1},                      // scalars → readouts, tests
  "series":   {"p": [0.5, 0.25], "labels": ["x1", "x2"]},   // arrays → bar/line charts
  "matrices": {"W": [[0.7, 0.3]]},                          // 2-D → heatmaps
  "tables":   {"contrib": {"cols": ["i", "p_i"], "rows": [[1, 0.5]]}} }
```

### Paths
Visuals, tests and explorations reference data by dotted path into the compute output:
`values.H`, `series.p`, `matrices.W`, `tables.contrib`, `matrices.W.0` (row 0).
Prefix `state.` to read the input state instead (`state.n`).

### `visuals`
Every visual has `type`, `title`, optional `caption` (rich: what to look at).

| type | fields |
|---|---|
| `readouts` | `items: [{source, label, unit?, digits?}]` – big live numbers |
| `bar` | `series: [{source, label}]`, `x_labels?`, `x_label`, `y_label`, `y_min?`, `y_max?`, `digits?` |
| `line` | `x_source`, `series: [{source, label}]`, `x_label`, `y_label`, `y_min?`, `y_max?`, `marker?: {x, y?, label?}` (paths or numbers; no `y` → vertical line), `points?` |
| `heatmap` | `source`, `row_labels?`, `col_labels?`, `row_title?`, `col_title?`, `digits?`, `domain?: [lo, hi]`, `row_sums?` (bool) |
| `table` | `source` (→ `{cols, rows}`), `digits?` |
| `custom` | `height` (px in a 600-wide viewBox), `draw`: JS function expression `function draw(svg, state, out, h) {...}` – see below |

`custom` helper `h`: `h.el(tag, attrs, parent)` (SVG element), `h.text(x, y, str, attrs, parent)`,
`h.scale(d0, d1, r0, r1)` (linear), `h.fmt(v, digits)`, `h.color(i)`, `h.W` (600), `h.H`.
The SVG is cleared before every call. Errors are contained to that card.

### `explorations` (exactly 2)
```json
{ "title": "Certainty vs. equal odds", "kind": "guided" | "limitation",
  "state": {"n": 4, "p": [1, 0, 0, 0]},     // applied by the "Try it" button (partial ok)
  "change": "rich: what to change", "observe": "rich: what to look at", "why": "rich: why it happens",
  "expect": [{"source": "values.H", "equals": 0, "tol": 1e-6}] }   // verified by checks.py + shown live
```

### `tests`
```json
{ "name": "uniform over 4 gives 2 bits", "state": {"n": 4, "p": [1, 1, 1, 1]},
  "expect": [{"source": "values.H", "equals": 2, "tol": 1e-6}] }
```
`state` is merged over the defaults (then vectors/matrices resized). Each expectation uses
`equals` (+ `tol`, default 1e-6) and/or `min` / `max`. Tests should cover the checks named in
the brief. Invariants (rows sum to 1, output = weighted sum) are exposed as `values.*` errors and
tested with `max`.

### `source`
```json
{ "paper": "A Mathematical Theory of Communication", "authors": "C. E. Shannon", "year": "1948",
  "url": "https://...", "section": "Section 6", "equation": "rich",
  "supported": [{"claim": "rich", "quote": "verbatim from the excerpt"}],
  "simplifications": ["rich: what we chose/changed for this demo"],
  "context_note": "optional, set by the agent, e.g. 'Excerpt not available; based on the brief.'" }
```
`quote` must be verbatim from the excerpt – checks.py drops supported items whose quote is not
found. The page always adds a fixed disclaimer that the demo does not reproduce paper results.

## Notes for checks.py
* The template legitimately contains `http://www.w3.org/2000/svg` (SVG namespace) and the paper
  URL is shown as text. Static "offline" checks should look for **resource loads**:
  `<script src`, `<link`, `<img src="http`, `url(http`, `@import`, `fetch(`, `XMLHttpRequest`.
* Section markers present in the HTML: `id="intro" id="playground" id="explore" id="limits" id="source"`.
