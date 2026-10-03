"""Excerpt compression (Person A). Zero tokens: pure Python, runs before any LLM call.

Prompt tokens count toward the efficiency score, and hidden cases may carry a long
paper excerpt. We keep only the parts relevant to the focus:

1. clean: fix hyphenated line breaks, collapse whitespace, drop the reference list
2. split into paragraphs (or ~700-char windows if the text has no paragraph breaks)
3. score each chunk by how well it matches the focus (BM25-style), with bonuses for
   section numbers named in the focus and for equation-like lines
4. keep the best chunks up to a character budget, IN ORIGINAL ORDER, verbatim
   (quotes copied from the kept text are still verbatim quotes of the excerpt)
"""
import math
import re
from collections import Counter

_STOP = set("""a an and are as at be been but by can do does each for from has have how if in into
is it its may more most not of on or our over same see show shows so such than that the their
them then there these they this to under up use used using via was we what when where which while
who will with within without would you your learner learners explain explains page should""".split())


def clean(text):
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)            # hyphenated line breaks
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    # drop a trailing reference list (it never helps explain a mechanism)
    m = re.search(r"\n(references|bibliography)\s*\n", t, re.I)
    if m and m.start() > len(t) * 0.5:
        t = t[:m.start()]
    return t.strip()


def _chunks(t, win=700):
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) <= 2 and len(t) > 2 * win:     # no paragraph structure: use sentence windows
        sents = re.split(r"(?<=[.!?])\s+", t.replace("\n", " "))
        paras, cur = [], ""
        for s in sents:
            if cur and len(cur) + len(s) > win:
                paras.append(cur)
                cur = ""
            cur = (cur + " " + s).strip()
        if cur:
            paras.append(cur)
    out = []                                       # split very long paragraphs
    for p in paras:
        while len(p) > 2 * win:
            cut = p.rfind(". ", 0, win + 200)
            cut = cut + 1 if cut > win // 2 else win
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        out.append(p)
    return out


def _tokens(s):
    return [w for w in re.findall(r"[a-z][a-z0-9]+|\d+(?:\.\d+)*", s.lower()) if w not in _STOP]


def _mentions(chunk, sec):
    """Does the chunk contain section/equation `sec` (e.g. 3.2.1, or 6 as a heading/eq. number)?"""
    e = re.escape(sec)
    if "." in sec:
        return re.search(rf"(?<![\d.]){e}(?![\d])", chunk) is not None
    return re.search(rf"(?m)^\s*{e}[.\s]|section\s+{e}\b|\({e}\)", chunk, re.I) is not None


def compress(excerpt, focus, budget_chars=6000, min_chars=2500):
    """Return (text, info). If the cleaned excerpt fits the budget it is kept whole."""
    t = clean(excerpt)
    info = {"orig_chars": len(excerpt), "clean_chars": len(t)}
    if len(t) <= budget_chars:
        info.update(kept_chars=len(t), chunks_kept="all")
        return t, info

    chunks = _chunks(t)
    q = Counter(_tokens(focus))
    sections = set(re.findall(r"\b\d+(?:\.\d+)+\b", focus))
    sections |= set(re.findall(r"\b(?:section|sec\.?|eq\.?|equation)\s*\(?(\d+(?:\.\d+)*)", focus, re.I))
    docs = [_tokens(c) for c in chunks]
    N = len(docs)
    avg = sum(len(d) for d in docs) / max(1, N)
    df = Counter(w for d in docs for w in set(d))

    scores = []
    for i, (c, d) in enumerate(zip(chunks, docs)):
        tf = Counter(d)
        s = 0.0
        for w, qw in q.items():
            if w in tf:
                idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                f = tf[w]
                s += qw * idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(d) / max(1, avg)))
        if any(_mentions(c, sec) for sec in sections):
            s += 3.0                                      # section/equation named in the focus
        if re.search(r"[=Σ∑√≤≥×]|\\frac|softmax|\(\d+\)\s*$", c, re.M):
            s += 0.8                                      # looks like it holds the math
        scores.append(s)

    top = max(scores) if scores else 0
    floor = 0.3 * top                     # only chunks reasonably close to the best match
    keep, used = set(), 0
    for i in sorted(range(N), key=lambda i: -scores[i]):
        if keep and scores[i] < floor and (used >= min_chars or scores[i] <= 0):
            break
        if keep and used + len(chunks[i]) > budget_chars:
            continue
        keep.add(i)
        used += len(chunks[i]) + 2
    # a short paragraph right after the best chunk often completes the definition / equation
    best = max(range(N), key=lambda i: scores[i]) if N else -1
    if 0 <= best + 1 < N and len(chunks[best + 1]) < 600 and used + len(chunks[best + 1]) <= budget_chars:
        keep.add(best + 1)

    kept = [chunks[i] for i in sorted(keep)]
    out, prev = [], -2
    for i in sorted(keep):                                # mark gaps so the model knows text was cut
        if out and i != prev + 1:
            out.append("[...]")
        out.append(chunks[i])
        prev = i
    text = "\n\n".join(out)
    info.update(kept_chars=len(text), chunks_total=N, chunks_kept=len(kept))
    return text, info
