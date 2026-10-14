# End-to-end verification — October 14, 2026

The local symposium chatbot is running and verified with the existing OpenAI/Upstash credentials. Credentials were not included in this report, source files or captured API responses.

## Active configuration

| Component | Verified value |
|---|---|
| Chat model | `gpt-4o-mini` from `LLM_MODEL` |
| Embedding model | `text-embedding-3-small` |
| Corpus | `data/symposium.pdf`, 5 pages |
| Vector index | 9 chunks, 1,536 dimensions |
| Cache | Persistent local SQLite semantic cache; threshold 0.15, TTL 86,400 seconds, 256 entries per namespace |
| Client admission | Upstash Redis, 10 requests per 60 seconds |
| Provider-work admission | Upstash Redis, 60 requests per 60 seconds globally |
| Backend | http://127.0.0.1:8000 |
| Frontend | http://127.0.0.1:8502 |

## Changes and causes

The original backend hardcoded `gpt-3.5-turbo` and did not consume the configured model or Redis limits. It also required an unavailable local Valkey service at startup. The frontend assumed every response succeeded, and cached responses lost their PDF source pages.

Configuration now validates and consumes the relevant settings. Atomic Redis admission runs before expensive work and fails closed. Optional Valkey caching has an explicit persistent local fallback. Sources survive cache hits; question validation, async provider calls, timeout/error responses, one-based page citations and citation-label validation are wired through the API and frontend.

Ingestion rebuilt the corpus through real OpenAI embeddings. FAISS metadata is stored in JSON and verified against a manifest, PDF hash and embedding model. The legacy pickle is no longer loaded. Paths resolve against the project root. The launcher, environment example and README document the actual behavior.

## Fresh checks

| Proof | Result |
|---|---|
| Regression suite | **33 passed**, 4 dependency deprecation warnings |
| `pip check` | **No broken requirements found** |
| Syntax compilation | Passed for config, ingestion, retrieval, demo, tests and verification script |
| Live `/health` | HTTP 200, configured model, dimensions and Redis limits reported correctly |
| Generated answer | Innovate 2026 date: March 15, 2026, cited to Page 1 |
| Exact cache hit | Preserved answer/sources, similarity 1.0 |
| Semantic cache hit | Paraphrase preserved answer/sources, similarity 0.8752 |
| Unsupported question | Explicit document-only refusal, no sources |
| Different intents | Venue and eligibility both missed the date cache and returned their own cited answers |
| Empty question | HTTP 422 |
| Client quota | HTTP 429 with `Retry-After` |
| Redis concurrency | Exactly 3 admissions out of 10 simultaneous attempts at limit 3 |
| Shared Redis state | A second limiter client was denied by the same bucket |
| Redis expiry | Admission restored after expiry |
| Invalid Redis token | Request blocked with credential-safe error |
| Visible frontend | Answer, repeated-question cached badge and retained source pages observed in browser |
| Credential scan | 26 source/document/evidence text files scanned; no configured OpenAI key or Redis token found |

The main live run passed all 11 checks. The recorded generation request took 2.265 seconds, the exact cached response took 0.011 seconds, and the paraphrase hit took 0.351 seconds. These are single-run observations on this machine, not throughput or production latency guarantees.

Regression coverage includes model/limit validation, persistent semantic hits, expiry and bounded storage, Redis failures and malformed results, admission before provider calls, cache source retention, rejection of invalid generation, refusal non-caching, Streamlit failure rendering, and rejection of changed PDF/index/metadata or embedding-model mismatches.

Publication verification also reproduced and fixed timestamp ties during cache eviction. Eviction now uses SQLite insertion order, with a deterministic tied-timestamp regression case. The full suite passed again after the fix. Git attributes preserve the indexed JSON bytes so document hashes remain valid across Windows and Linux checkouts.

## Evidence files

- `live-verification.json`: real API responses, timings, limiter results and checks.
- `different-intents.json`: venue and eligibility responses under the final threshold.
- `frontend-cache-hit.jpg`: browser screenshot showing the cached answer and source pages.
- `../vector_store/manifest.json`: corpus/index fingerprints and dimensions.

## Practical limits

- Docker/Valkey was unavailable. The optional Valkey/BetterDB branch is retained but has no live verification evidence. The verified runtime uses SQLite semantic caching and Upstash Redis rate limiting. BetterDB requires a Valkey search module; the supplied Upstash REST credentials are for admission.
- The local Streamlit server shares one client-address bucket among browser users. Public deployment requires authentication and deliberate proxy/identity handling.
- Cosine similarity is a heuristic. Venue, eligibility and the unsupported question were checked, but these examples are not a comprehensive false-hit evaluation. Citation labels validate source membership, not factual entailment.
- Local cache dollar savings are `null`; no pricing-based estimate was fabricated.
- The supplied PDF emits pypdf object-offset repair warnings during extraction; all five pages and nine chunks were extracted and verified.
- Four automated-test warnings come from Starlette/AnyIO and FAISS SWIG deprecations; no tests failed.
- This report records the local verification before GitHub publication. It does not claim a public deployment or independent review.

## Repeat the checks

From the nested project folder, run `.\run.ps1 test`, `.\.venv\Scripts\python.exe -m pip check`, and `.\run.ps1 verify` with the backend running. Live verification makes real provider calls and intentionally consumes the local client's quota. Allow its normal 60-second window to expire before repeating. Redis concurrency tests use unique, short-lived keys and do not flush shared data.
