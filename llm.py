"""OpenRouter client (Person A).

One function the rest of the agent uses: LLM.chat(messages, max_tokens, stage).
- counts every request (retries included) BEFORE sending it
- logs the REAL usage numbers from the response (graders compare them to OpenRouter)
- never raises: returns None on failure so the main loop decides what to do
"""
import os
import time

import requests

BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
API_URL = BASE_URL + "/chat/completions"

# Thinking tokens count as completion tokens, so by default we turn thinking off.
# AGENT_REASONING: disabled (default: no thinking) | low | medium | high | off (send nothing).
# Tested on deepseek/deepseek-v4.1-flash: disabled = same quality, ~1/3 tokens, ~3.5x faster than low.
# The graded run uses the DEFAULT, so set the default to whatever wins in testing.
REASONING_EFFORT = os.environ.get("AGENT_REASONING", "disabled").strip().lower()

# Latency is scored: ask OpenRouter to route to the highest-throughput provider of this model.
# AGENT_PROVIDER_SORT=off disables it (e.g. if a model/provider rejects the field).
PROVIDER_SORT = os.environ.get("AGENT_PROVIDER_SORT", "throughput").strip().lower()

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class LLMResult:
    def __init__(self, text, finish_reason, prompt_tokens, completion_tokens):
        self.text = text
        self.finish_reason = finish_reason
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    @property
    def truncated(self):
        return self.finish_reason == "length"


class LLM:
    def __init__(self, api_key, model, tracer):
        self.api_key = api_key
        self.model = model
        self.tracer = tracer
        self.send_reasoning = REASONING_EFFORT not in ("off", "none", "")
        self.send_provider = PROVIDER_SORT not in ("off", "none", "")

    def _body(self, messages, max_tokens, temperature):
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.send_reasoning:
            if REASONING_EFFORT in ("disabled", "false", "0"):
                body["reasoning"] = {"enabled": False}   # no thinking tokens at all
            else:
                body["reasoning"] = {"effort": REASONING_EFFORT, "exclude": True}
        if self.send_provider:
            body["provider"] = {"sort": PROVIDER_SORT}
        return body

    def chat(self, messages, max_tokens, stage, temperature=0.2, attempts=2):
        tr = self.tracer
        for attempt in range(1, attempts + 1):
            ok, why = tr.can_call()
            if not ok:
                tr.log(stage, "budget_guard", "stop", reason=why)
                return None

            mt = int(min(max_tokens, tr.remaining_tokens()))
            timeout = max(20, min(240, tr.remaining_seconds() - 10))
            tr.calls += 1
            call_no = tr.calls
            t = time.time()
            try:
                r = requests.post(
                    API_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "X-Title": "Paper to Playground agent",
                    },
                    json=self._body(messages, mt, temperature),
                    timeout=timeout,
                )
            except requests.RequestException as e:
                tr.log(stage, "llm_call", "error", call=call_no, attempt=attempt,
                       error=type(e).__name__, elapsed_s=round(time.time() - t, 2))
                continue

            elapsed = round(time.time() - t, 2)
            try:
                data = r.json()
            except ValueError:
                data = {}

            usage = data.get("usage") or {}
            p_tok = usage.get("prompt_tokens") or 0
            c_tok = usage.get("completion_tokens") or 0
            tr.record_usage(p_tok, c_tok)

            err = data.get("error")
            if r.status_code != 200 or err or not data.get("choices"):
                msg = (err.get("message") if isinstance(err, dict) else str(err or r.text))[:300]
                tr.log(stage, "llm_call", "error", call=call_no, attempt=attempt,
                       http_status=r.status_code, error=msg,
                       prompt_tokens=p_tok, completion_tokens=c_tok, elapsed_s=elapsed)
                # Model rejected the reasoning parameter -> drop it and retry once.
                if r.status_code == 400 and self.send_reasoning and "reason" in msg.lower():
                    self.send_reasoning = False
                    tr.log(stage, "config", "reasoning_param_disabled")
                    continue
                if r.status_code == 400 and self.send_provider and "provider" in msg.lower():
                    self.send_provider = False
                    tr.log(stage, "config", "provider_sort_disabled")
                    continue
                if r.status_code in RETRYABLE_STATUS or r.status_code == 200:
                    if attempt < attempts:
                        time.sleep(min(3, 1.5 * attempt))
                    continue
                return None

            choice = data["choices"][0]
            text = (choice.get("message") or {}).get("content") or ""
            if isinstance(text, list):  # some providers return content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            finish = choice.get("finish_reason") or choice.get("native_finish_reason")
            details = usage.get("completion_tokens_details") or {}

            tr.log(stage, "llm_call", "ok" if text.strip() else "empty",
                   call=call_no, attempt=attempt,
                   generation_id=data.get("id"), model=data.get("model"),
                   prompt_tokens=p_tok, completion_tokens=c_tok,
                   reasoning_tokens=details.get("reasoning_tokens"),  # a count only, never the text
                   elapsed_s=elapsed, finish_reason=finish, output_chars=len(text))

            if not text.strip():
                continue  # e.g. all tokens spent on reasoning; retry if budget allows
            return LLMResult(text, finish, p_tok, c_tok)
        return None
