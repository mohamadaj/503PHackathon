# Paper to Playground: agentic explainer generator

Given a research-paper case (`case.json`), the agent produces one self-contained interactive explainer page (`out/index.html`) and a trace of every step (`out/trace.jsonl`).

## Team
- Mohamad El Ajouz: agent core (CLI, OpenRouter client, trace + budget guard, extraction, main loop)
- TODO (Person B): page template, widgets, charts, `render.py`
- TODO (Person C): prompts, schema, `checks.py` JS harness, test cases

## Run
```bash
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1   (or venv\bin\Activate.ps1)   Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
# Windows PowerShell: $env:OPENROUTER_API_KEY="sk-or-..."   Linux/macOS: export OPENROUTER_API_KEY=sk-or-...
python agent.py --input examples/entropy/case.json --output out --model MODEL_ID
```
Check the page with `cd out && python -m http.server` and open http://localhost:8000 in Chromium.

**Model tested with:** `deepseek/deepseek-v4.1-flash` (DeepSeek V4.1 Flash via OpenRouter)

## Architecture
```
case.json -> load -> context (excerpt or focus only; no network fetch)
          -> GENERATE (1 LLM call: JSON spec + JS compute())
          -> extract (fenced blocks, lenient JSON repair)
          -> CHECK (local, 0 tokens: structure, JS runs, tests, no external URLs)
          -> REVISE (only if checks fail; only broken fields sent; max 2) -> CHECK
          -> render spec into a fixed HTML template -> out/index.html
tracer.jsonl records every step; a budget guard stops at 8 calls / 480 s / 26k completion tokens
(below the 10 / 600 s / 30k limits).
```
Typical run: 1-2 LLM calls.

| File | Role |
|---|---|
| `agent.py` | CLI and main loop |
| `llm.py` | OpenRouter client, real usage logging, retries inside budget |
| `tracer.py` | JSONL trace + budget guard (never logs the key or model reasoning) |
| `extract.py` | robust JSON/JS extraction from model replies |
| `prompts/` | generation and revision prompts |
| `checks.py` | deterministic checks on the spec |
| `render.py`, `templates/` | spec -> self-contained HTML |

Exit codes: `0` page written from a spec, `1` generation failed (fallback page written), `2` bad input or missing key.

## Example
`examples/entropy/case.json` -> `examples/entropy/out/` (TODO: commit one real run)

## Credits
TODO: list any reused code/libraries. AI coding assistants were used during development.
