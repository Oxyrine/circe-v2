"""ai/guard.py: PII redaction and prompt-injection checks. Every LLM path goes through here.

Pure stdlib, pure functions. Redaction runs BEFORE any text leaves the process.
"""

import re
from collections import namedtuple

MAX_QUESTION_CHARS = 500

# Order matters: GSTIN embeds a PAN, and "+91" + 10-digit phones look like 12-digit Aadhaar.
_PII_PATTERNS = (
    ("GSTIN", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("PHONE", re.compile(r"(?<![\d+])(?:\+91[\s-]?|0)?[6-9]\d{9}(?!\d)")),
    ("AADHAAR", re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")),
    ("DIN", re.compile(r"\bDIN[\s:#-]*\d{7,8}\b", re.IGNORECASE)),
)

_INJECTION_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instruction|prompt|rule|system|previous|above|prior)",
    r"\b(reveal|show|print|repeat|leak)\b.{0,30}\b(system|hidden|initial)\b.{0,15}\b(prompt|instruction)",
    r"\bsystem\s+prompt\b",
    r"\byou\s+are\s+now\b",
    r"\b(act|behave|pretend)\s+(as|like)\b",
    r"\b(developer|jailbreak|dan)\s+mode\b",
    r"</?\s*(system|assistant|user|instruction)s?\s*>",
    r"\b(mark|set|change|classify|label|flag)\b.{0,30}\b(ring|it|this)\b.{0,25}\b(clean|cleared|benign|safe|legitimate|fraud)",
    r"\b(change|edit|modify|lower|raise|set)\b.{0,20}\b(score|threshold|aggregate)\b",
))

Verdict = namedtuple("Verdict", "ok text reason")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact(text, names=None):
    """Mask PII patterns and alias known names. `names` maps a name/address to an alias."""
    if not text:
        return text
    out = str(text)
    for name, alias in sorted((names or {}).items(), key=lambda kv: -len(kv[0])):
        if name:
            out = re.sub(re.escape(name), alias, out, flags=re.IGNORECASE)
    for label, pat in _PII_PATTERNS:
        out = pat.sub(f"[{label}]", out)
    return out


def check_question(question):
    """Validate untrusted chat text. Returns Verdict(ok, cleaned_text, reason_code)."""
    if not isinstance(question, str):
        return Verdict(False, "", "invalid")
    q = _CONTROL.sub(" ", question).strip()
    if not q:
        return Verdict(False, "", "empty")
    if len(q) > MAX_QUESTION_CHARS:
        return Verdict(False, "", "too_long")
    if any(p.search(q) for p in _INJECTION_PATTERNS):
        return Verdict(False, "", "blocked")
    return Verdict(True, redact(q), "")


def names_from_entities(entities, ids=None):
    """Alias map {company name / address: 'Entity-E001'} for the given entity ids (or all)."""
    names = {}
    for eid, e in entities.items():
        if ids is not None and eid not in ids:
            continue
        for field in ("name", "address"):
            if e.get(field):
                names[e[field]] = f"Entity-{eid}"
    return names
