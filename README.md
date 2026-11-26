# Circe: a fraud-review assistant for credit-risk analysts

Circe finds circular-trading fraud in invoice data, ranks the suspicious loops by **one analyst's own review rules**, and answers questions about each loop from its scored evidence.

**Live:** https://circe-v2.vercel.app · **Vertical:** fintech, financial risk · **Persona:** credit-risk analyst at an invoice-discounting (TReDS) platform

> The core rule: **code decides, the language model explains.** No model output can change a score, a classification or a queue position.

## Contents

1. [Vertical, persona and problem](#1-vertical-persona-and-problem)
2. [How Circe meets the challenge criteria](#2-how-circe-meets-the-challenge-criteria)
3. [What makes it different](#3-what-makes-it-different)
4. [Architecture](#4-architecture)
5. [Detection and scoring](#5-detection-and-scoring)
6. [Context-driven decisions](#6-context-driven-decisions)
7. [The assistant](#7-the-assistant)
8. [Google services](#8-google-services)
9. [Security](#9-security)
10. [Efficiency](#10-efficiency)
11. [Testing](#11-testing)
12. [Accessibility](#12-accessibility)
13. [Measured results](#13-measured-results)
14. [Run, configure, deploy](#14-run-configure-deploy)
15. [API reference](#15-api-reference)
16. [Assumptions and limitations](#16-assumptions-and-limitations)

## 1. Vertical, persona and problem

On a TReDS platform, suppliers get early cash by discounting their invoices. In **circular trading**, connected firms bill each other in a loop (`A → B → C → A`) to inflate turnover or to finance receivables with no real trade behind them.

The analyst has two problems:

| Problem | Why it is hard | What Circe does |
|---|---|---|
| **Too many loops.** The sample ledger has 60 firms and 172 invoices, which yield 5,542 candidate loops. | Real supply chains contain loops too, so finding a cycle proves nothing. | Scores each loop on four independent signals. 5 cross the default flag threshold. |
| **The missing leg.** A loop can be left open on the platform and closed elsewhere. | A cycle search over invoices alone cannot see it. | Closes open invoice paths using shared directors, addresses or registration dates. |

The analyst also has limited time, their own risk appetite and a sector desk. A single fixed ranking ignores all three.

## 2. How Circe meets the challenge criteria

| Criterion | Implementation | Verify in |
|---|---|---|
| **Smart, dynamic assistant** | Per-ring Q&A that reflects the analyst's current settings, with a two-provider fallback and a no-AI answer path | `ai/assistant.py`, `ai/provider.py`, `demo/assistant.js` |
| **Logical decisions based on user context** | Four user inputs change the flagged set, order and bucket; each placement lists its reasons | `pipeline/prioritise.py`, `pipeline/tests/test_prioritise.py` |
| **Practical, real-world usability** | A daily work queue (escalate, review today, backlog, other desk), saved settings, suggested questions, evidence memos, and ranking and explanations that work without an AI key | `demo/` |
| **Clean, maintainable code** | Decision logic in pure functions, one flag threshold on the server, one route table shared by the local server and Vercel, JSON Schema contracts | `pipeline/`, `contract/` |
| **One persona, one vertical** | Inputs, bucket names, prompts and page copy are written for a TReDS credit-risk analyst | this file, `SYSTEM_PROMPT` in `ai/assistant.py` |

## 3. What makes it different

1. **Corporate-graph closure.** An open invoice path can be completed by a `corporate_bridge` hop when its two end firms **directly** share a director, an exact registered address or an exact registration date. Evidence is pairwise and never chained through a third firm. If a real invoice already closes the pair, no bridge is claimed.
2. **Abstention instead of guessing.** A signal without enough data returns `None`. The aggregate renormalises the weights over the remaining signals, and the UI and assistant say which signals abstained.
3. **Explainable personal triage.** The same scored rings give different queues for different analysts, using deterministic rules and plain-language reasons.
4. **The model sits outside the decision path.** It receives a fact sheet built on the server. A chat answer that names another ring or uses wording that contradicts the classification is discarded.
5. **It degrades in steps.** Gemini, then Groq, then an answer assembled from the same facts. A reply cut off by a token limit is treated as a failure.
6. **No runtime SDKs.** Both model APIs use Python's standard library. The front end is plain HTML, CSS and JavaScript with no build step.

## 4. Architecture

```
data/entities.json, data/invoices.json   (synthetic)
        │
graph/      canonicalise → transaction graph → Tarjan SCC → bounded DFS cycles
            └─ corporate bridge for open paths
        │
scoring/    value · product · timing · externality → weighted geometric mean
        │
pipeline/   base_rings.json (precomputed subset)  ·  run_rescore() (live)
        │
        ├── prioritise.py ◄── analyst context        DECIDES  (pure, no I/O)
        │
        └── ai/guard.py → ai/assistant.py → ai/provider.py    EXPLAINS
                                             Gemini → Groq → deterministic
        │
pipeline/http_common.py   route table, body cap, headers, rate limit
        │
server.py (local)   ·   api/*.py (Vercel functions)
        │
demo/       queue, network map, entity directory, ledger, context panel, chat
```

| Path | Responsibility |
|---|---|
| `graph/` | Entity canonicalisation, Tarjan SCC, cycle search, corporate closure |
| `scoring/` | The four signals, aggregation, evidence strings, benchmark helpers |
| `pipeline/` | Service layer, prioritiser, shared HTTP layer, ring cache |
| `ai/` | PII redaction, injection checks, grounded Q&A, memo generation |
| `api/` | One thin Vercel handler per route |
| `demo/` | Front end |
| `contract/` | JSON Schemas and validator ([wire protocol](WIRE_PROTOCOL.md)) |
| `data/` | Synthetic economy generator, fraud injector, ground truth |
| `exasol/` | Optional database backend |

## 5. Detection and scoring

### 5.1 Candidate generation

1. **Canonicalisation.** Two records merge only if their normalised name **and** address are identical. There is no fuzzy threshold, because a wrong merge would corrupt net-position arithmetic.
2. **SCC partitioning.** An iterative Tarjan implementation, `O(V + E)`, with no recursion limit to hit.
3. **Cycle search.** An iterative, depth-limited DFS for simple cycles of 3 to 8 firms. Each cycle is explored only from its lexicographically smallest node, so it is found once. Budgets per SCC are 50,000 cycles and 2,000,000 expansions.
4. **Corporate closure.** Simple paths between bridgeable pairs, with budgets of 5,000 paths and 200,000 steps per pair.

When a budget is reached, the search logs a truncation warning. Candidates are deduplicated by canonical key, and a transaction-closed ring wins over a corporate-closed one.

### 5.2 Signals

All signals are in `[0, 1]`; higher means more consistent with circular trading. Definitions are in `scoring/scoring.py`.

| Signal | Definition | Abstains when |
|---|---|---|
| `value` | `exp(−(Σ\|netᵢ\| / Σ invoice value) / 0.15)`, where `netᵢ` is the net position of each firm with both an incoming and an outgoing invoice. Near 1 when little value stays with them. | No such firm, no invoice hops, or zero value |
| `product` | Mean HS-code similarity of consecutive invoice hops: identical 1.0, same 4-digit prefix 0.7, same 2-digit prefix 0.3, otherwise 0 | No comparable pair of HS codes |
| `timing` | `w·(1 − min(CV/0.60, 1)) + (1−w)·exp(−mean_gap/30)` over the days between consecutive invoices, with `w = 0.5·min(1, (gaps−1)/4)`. All-zero gaps score 1.0. | Fewer than 3 invoice hops, or a missing or invalid date |
| `externality` | Share of the member firms' total invoice value that stays inside the ring. It measures economic isolation. | The members have no invoice volume |

### 5.3 Aggregate and exposure

```
aggregate     = exp( Σ wₖ · ln(max(sₖ, 1e−9)) / Σ wₖ )     over non-abstained signals, wₖ = 0.25
expected_loss = round( unrounded aggregate × Σ invoice value in the ring )
```

If every signal abstains, the aggregate is 0. The geometric mean lets one very low signal pull the result down sharply. A ring is flagged by default at **0.70** (`FLAG_THRESHOLD`). `expected_loss` is a ranking weight, not a calibrated probability of loss.

An additional industry note reports a services-class seller issuing an invoice with a goods HS code. It appears as evidence and does **not** change the score.

## 6. Context-driven decisions

`prioritise(rings, entities, context)` is a pure function.

| Input | Validation | Effect |
|---|---|---|
| `risk_threshold` | Number, clamped to 0.50–0.90, default 0.70 | Decides which rings are flagged for this analyst |
| `exposure_limit_inr` | Non-negative number up to 10¹², optional | Flagged rings at or above it are escalated |
| `sectors` | Only sectors present in the data; empty means all | A ring with no member in these sectors goes to another desk |
| `daily_capacity` | Integer, clamped to 1–50, default 10 | Limits "review today"; the rest becomes backlog |

Each ring takes the **first** rule that applies:

```
score < risk_threshold                  → cleared (counted, not queued)
sectors set and no member matches       → other_desk
limit set and expected_loss ≥ limit     → escalate        (not limited by capacity)
otherwise                               → review_today    (overflow → backlog)
```

Buckets are ordered `escalate`, `review_today`, `backlog`, `other_desk`, then by exposure. The sector rule runs before escalation, so a large out-of-sector ring is routed to the other desk.

Real output for threshold 0.70, sector `manufacturing`, capacity 2:

| Rank | Ring | Bucket | Last reason shown |
|---|---|---|---|
| 1 | R5538 | review_today | Within your daily capacity of 2 |
| 2 | R5542 | review_today | Within your daily capacity of 2 |
| 3 | R5540 | backlog | Beyond your daily capacity of 2 |
| 4 | R5541 | other_desk | No entity in your sectors; route to the desk that covers them |
| 5 | R5539 | other_desk | No entity in your sectors; route to the desk that covers them |

R5541 has the largest exposure of the five, yet ranks fourth for this analyst. With no sector filter, it ranks first.

Raising the threshold also changes the flagged set on this data: 0.70 flags 5 rings, 0.82 flags 4 and 0.90 flags none.

Settings are saved in the browser. If the ranking API is unreachable, the page says so and falls back to exposure order. The ranking rules are not duplicated in JavaScript.

## 7. The assistant

### 7.1 Request path

```
question
  → guard.check_question      type, empty, 500-character cap, injection patterns, PII masking
  → get_ring(ring_id)         server-side lookup; client ring data is never accepted
  → build_facts               scores and their meanings, abstentions, evidence, closure meaning,
                              exposure, and this analyst's bucket and reasons
  → alias company names       question and earlier questions included
  → Gemini → Groq             first reply that passes answer_ok
  → otherwise                 deterministic answer from the same facts
```

The response carries `source: "llm"` or `"deterministic"`, and the UI labels every answer accordingly.

### 7.2 Grounding controls

- The system prompt limits answers to the `FACTS` block, requires "not in the evidence" for anything else, forbids changing the classification or giving legal advice, and marks `<question>` text as untrusted.
- Jargon is defined inside the facts, so the model does not have to guess what a closure type or signal means.
- At most the last three **questions** are sent as history, each checked again. Answers supplied by the client are never replayed to the model.
- `answer_ok` rejects empty replies, other ring IDs, and phrases that contradict a flagged or cleared status.
- Without a working model, keyword intents answer: why flagged, which signals abstained, what the closure means, the scores, and what to do next.

### 7.3 Evidence memo

`generate_narrative` asks for a plain-text memo under 250 words, with no dates, recipients, historical comparisons, intent allegations or actions. Evidence is redacted first. If both providers fail, a templated memo is returned.

These memo limits are **prompt instructions**. The `answer_ok` check applies to chat only.

## 8. Google services

| Service | Use |
|---|---|
| **Gemini API** (`gemini-3.5-flash-lite`) | Primary model for chat answers and evidence memos. REST `generateContent` with `systemInstruction`, temperature 0.2, a 1,500-token cap, low thinking level on Gemini 3 models, a 10 s timeout, and the key in the `x-goog-api-key` header |
| **Google Fonts** | Interface typefaces, allowed explicitly by the Content Security Policy |

The fallback is Groq's `openai/gpt-oss-120b`. Override models with `GEMINI_MODEL` and `GROQ_MODEL`.

## 9. Security

| Threat | Control |
|---|---|
| Personal data reaching a model provider | GSTIN, PAN, email, Indian phone, Aadhaar and `DIN`-prefixed numbers are masked by pattern. Known company names and addresses become aliases such as `Entity-E001`. |
| Prompt injection | Pattern blocklist, length cap, delimited untrusted input, output check, and no decision authority for the model |
| Forged evidence from the client | Endpoints accept a ring ID and look the ring up on the server |
| Oversized or malformed requests | 16 KB body cap; `Content-Length` and JSON validated; JSON error responses |
| Leaked keys or internals | Keys come from environment variables; model keys travel in headers; model, rescore and unhandled route errors return fixed messages and log the exception type |
| Local file exposure | The local server returns 404 for any dotfile or dot-directory, including `.env` and `.git` |
| Cross-origin use from browsers | No CORS grant |
| Browser attacks | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`; HSTS on Vercel |
| Script injection from model text | Chat answers are written with `textContent` |
| Quota abuse | 20 requests per minute per IP across the chat and memo routes |

**Known gaps:** a blocklist can be bypassed, and `answer_ok` does not verify every number or claim. The CSP still allows inline scripts and styles. The rate limit is held in memory, so each Vercel instance counts separately, and the `X-Forwarded-For` value it keys on can be spoofed on a directly exposed local server. `/api/rescore` is not rate-limited. The local-only `/api/audit` route (Exasol) can return exception text. The local server listens on all network interfaces. There is no user authentication, since the data is synthetic.

## 10. Efficiency

- **Precomputed ring cache.** `pipeline/base_rings.json` holds 60 of the 5,542 scored rings: every ring scoring at least 0.50, the 60 largest by exposure and every ring in the demo bundle (these sets overlap). It loads in about 0.2 s on the development machine, compared with about 3.6 s to detect and score everything there. A test checks that the cache is current.
- **In-process caching** of the loaded rings with `lru_cache`.
- **Bounded work:** search budgets, a 200-item response cap, a 16 KB request cap and 10 s timeouts per model call. Two stalled providers can therefore delay an answer by about 20 s.
- **Small footprint:** `requirements.txt` lists only `jsonschema` and `pytest`; the front end ships without a bundler.

## 11. Testing

169 tests. 168 run anywhere; the Exasol parity test needs a live database and **fails without one**.

```bash
python -m pytest -q
python -m pytest -q --deselect exasol/tests/test_parity.py::test_exasol_parity_when_live
```

| Area | Tests | Covers |
|---|---|---|
| `ai/` | 53 | PII patterns, injection, grounding, output rejection, provider fallback, truncation, no key or error leakage, prompt redaction |
| `graph/` | 62 | SCC, cycles, depth limits and budgets, corporate closure, canonicalisation, malformed data |
| `pipeline/` | 37 | Ranking rules, HTTP integration on a real socket, headers, rate limit, dotfile blocking, cache freshness, Vercel entrypoints |
| `data/generator/` | 10 | Synthetic data generator |
| `exasol/` | 6 | UDF logic and parity |
| `scoring/` | 1 | Scoring checks |

Provider tests replace the network calls with stubs. CI (`.github/workflows/ci.yml`) validates data against the JSON Schemas and runs pytest on each push to `main`.

## 12. Accessibility

- Ring cards are native buttons with `aria-expanded`.
- New form controls have labels, and sector checkboxes are grouped in a `fieldset`.
- `aria-live` regions announce queue updates, the rescore banner, memos and chat replies.
- Dialogs have `role="dialog"` and `aria-modal`, trap Tab, close on Escape and return focus.
- Reduced-motion preferences are handled in the stylesheet.
- Text in the context panel and chat was checked for 4.5:1 contrast, and the one failing badge was fixed.

**Not yet done:** testing with a screen reader or a full-site contrast audit. The context panel and ring cards fit a 375 px screen, but the top bar and navigation still overflow it.

## 13. Measured results

Synthetic benchmark: 60 firms, 172 invoices, 6 injected fraud rings, 5,542 candidates (139 of them corporate-closed). A match means the firm sets have Jaccard similarity of at least 0.5.

| Measure | Result |
|---|---|
| Injected rings present among candidates | **6 of 6**, each with an exact firm-set match |
| Injected rings flagged at 0.70 | **5 of 6** |
| Flagged rings that are injected fraud | **5 of 5** |
| Injected ring with an omitted invoice leg | Recovered by corporate closure and flagged (R5538, 0.81) |
| Precision@6 when **all** candidates are sorted by exposure, ignoring the threshold | 4 of 6 |

**The miss:** injected ring T01 scores 0.38. Its `value` (0.90) and `product` (1.00) signals are high, but its members trade heavily outside the loop, so `externality` is 0.04 and the geometric mean falls. A ring that hides inside real trade can evade the current weights.

These figures describe one synthetic dataset with known injected patterns. They do not estimate accuracy on real platform data.

## 14. Run, configure, deploy

Python 3.11 or later.

```bash
pip install -r requirements.txt
python server.py 8000            # open http://localhost:8000/demo/
```

Serve the repository through `server.py` or Vercel. Opening `index.html` from disk does not work because assets use absolute `/demo/...` paths.

For AI answers, copy `.env.example` to `.env` and add either key or both. `server.py` reads the file; real environment variables take precedence. `.env` is git-ignored.

| Variable | Required | Default |
|---|---|---|
| `GEMINI_API_KEY` | No | none |
| `GROQ_API_KEY` | No | none |
| `GEMINI_MODEL` | No | `gemini-3.5-flash-lite` |
| `GROQ_MODEL` | No | `openai/gpt-oss-120b` |

**Using it:** enter the investigation, set **My review context**, open a ring, then use **Ask about this ring** or **Generate AI Evidence Narrative**.

**Vercel:** pushing to `main` deploys the linked project. Add the keys under *Settings → Environment Variables*, then redeploy. Each `api/*.py` file must declare a literal `class handler` and must not export `app` or `application`; a test enforces this.

After changing `data/`, run `python -m pipeline.build_base_rings`.

## 15. API reference

All bodies are JSON.

| Route | Request | Success response |
|---|---|---|
| `POST /api/prioritise` | `{context}` | `{context, ranked[{ring_id, rank, bucket, reasons, aggregate, expected_loss}], flagged_count, cleared_count}` |
| `POST /api/assistant` | `{ring_id, question, context, history}` | `{answer, source, grounded_on, ring_id}` |
| `POST /api/ai/narrative` | `{ring_id}` | `{ring_id, status, ai_available, narrative, governance_note}` |
| `POST /api/rescore` | `{investigator_invoices}` | `{rings, total_candidate_count, high_risk_count, schema_version, source}` |
| `GET /api/health` | none | `{status}` |

```bash
curl -X POST http://localhost:8000/api/assistant \
  -H "Content-Type: application/json" \
  -d '{"ring_id":"R5541","question":"Why is this flagged?","context":{"risk_threshold":0.8}}'
```

Errors: `400` invalid input or rejected question, `404` unknown ring, `413` body too large, `429` rate limited, `500` fixed message. The assistant answers only about rings in the cached set, so a ring created by a live rescore may return `404`.

## 16. Assumptions and limitations

- **All data is synthetic**, generated from a modelled sector economy with injected fraud. No real firms or platform feeds are used.
- A flag is a **reason to review**, not a finding of fraud and not legal advice.
- A shared director, address or registration date is a lead. It does not prove common control, and shared registration dates are the weakest of the three.
- Weights and constants are hand-set. The repository's own benchmark helper recommends reviewing the weights because of the top-6 result above; they have not been retuned.
- One analyst context is stored per browser; there are no accounts or shared audit history in the Vercel deployment.
- **Exasol is optional.** Circe began as an Exasol AI + Data Challenge 2026 entry. The database backend, parity check and audit trail remain in [`exasol/`](exasol/README.md) for local use and are not part of the deployed app.

Also see [SECURITY.md](SECURITY.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
