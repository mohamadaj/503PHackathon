"""Trace logger + budget guard (Person A).

Every event becomes one JSON line in out/trace.jsonl with at least
stage / action / result. The same object also enforces the per-case
budget, because every LLM call touches both.
"""
import json
import time

# Official assessment limits: 10 requests, 600 s, 30,000 completion tokens per case.
# We stop well below them so a slow or verbose call can't push us over.
MAX_CALLS = 8
MAX_SECONDS = 480
MAX_COMPLETION_TOKENS = 26000

# Keys that must never appear in the trace (credentials / hidden reasoning).
_FORBIDDEN_KEYS = {
    "api_key", "key", "authorization", "headers",
    "reasoning", "reasoning_content", "reasoning_details", "thinking",
}


class Tracer:
    def __init__(self, path, secret=None):
        self.path = path
        self.f = open(path, "w", encoding="utf-8")
        self.t0 = time.time()
        self.secret = secret
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.revisions = 0

    # ---------- logging ----------
    def log(self, stage, action, result, **extra):
        rec = {
            "t": round(self.elapsed(), 2),
            "stage": stage,
            "action": action,
            "result": result,
        }
        for k, v in extra.items():
            if k.lower() in _FORBIDDEN_KEYS:
                continue
            rec[k] = v
        line = json.dumps(rec, ensure_ascii=False, default=str)
        if self.secret and self.secret in line:  # belt and braces
            line = line.replace(self.secret, "[redacted]")
        self.f.write(line + "\n")
        self.f.flush()  # if we crash later, the trace up to here survives

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass

    # ---------- budget ----------
    def elapsed(self):
        return time.time() - self.t0

    def remaining_seconds(self):
        return MAX_SECONDS - self.elapsed()

    def remaining_tokens(self):
        return MAX_COMPLETION_TOKENS - self.completion_tokens

    def can_call(self, min_tokens=800, min_seconds=25):
        """Return (ok, reason). Checked before EVERY request, retries included."""
        if self.calls >= MAX_CALLS:
            return False, f"call_cap_reached ({self.calls}/{MAX_CALLS})"
        if self.remaining_seconds() < min_seconds:
            return False, f"time_low ({self.elapsed():.0f}s used)"
        if self.remaining_tokens() < min_tokens:
            return False, f"token_budget_low ({self.completion_tokens} used)"
        return True, "ok"

    def record_usage(self, prompt_tokens, completion_tokens):
        self.prompt_tokens += int(prompt_tokens or 0)
        self.completion_tokens += int(completion_tokens or 0)

    def summary(self):
        return {
            "calls": self.calls,
            "total_prompt_tokens": self.prompt_tokens,
            "total_completion_tokens": self.completion_tokens,
            "revisions": self.revisions,
            "elapsed_s": round(self.elapsed(), 2),
        }
