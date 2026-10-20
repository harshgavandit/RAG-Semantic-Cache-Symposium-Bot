# Hosted verification — October 20, 2026

Public URL: https://symposium-rag-bot.vercel.app

The deployment was tested with ordinary unauthenticated HTTPS requests, without Vercel protection-bypass tokens. Both the page and API are publicly accessible.

## Active hosted configuration

- Python 3.12 FastAPI on Vercel with CDN-served HTML/CSS/JavaScript.
- Model: `gpt-4o-mini`; embeddings: `text-embedding-3-small`.
- Corpus: the generated fictional sample, 1 page, 1 chunk, 1,536 dimensions.
- Cache: shared Upstash Redis, bounded to 256 entries per namespace, TTL 86,400 seconds.
- Client admission: 10 questions per 60 seconds; global provider-work admission: 60 requests per 60 seconds.
- Original PDFs, extracted data, indexes, credentials, logs and local runtime caches were excluded from deployment uploads.

## Evidence

- The first real production chat call returned HTTP 200, `cache_hit: false`, and: "The Innovate 2026 symposium is scheduled for March 15, 2026 [Page 1]."
- Browser testing displayed the answer, cached badge and expanded Page 1 source. Screenshot: `vercel-desktop.jpg`.
- `vercel-live-verification.json` contains the 11 passing public API/Redis checks, actual responses and timings. Shared cache seeding means the recorded first/repeat forms are semantic hits (0.8751), while the recorded paraphrase form is an exact hit (1.0). Labels describe test order; similarity distinguishes exact from semantic reuse.
- Unsupported questions returned the exact document-only refusal with no sources.
- Blank input returned HTTP 422; quota exhaustion returned HTTP 429 with `Retry-After`.
- Isolated expiring Redis tests admitted exactly 3 of 10 concurrent attempts at limit 3, shared denial across two clients, restored admission at expiry and failed closed with an invalid token.
- Additional isolated tests of the shared cache passed cross-client exact/semantic reuse, source preservation, unrelated-question misses, shared statistics, bounded eviction and TTL expiry.
- The complete local regression suite passed 42 tests with four dependency deprecation warnings. Dependency consistency and JavaScript syntax checks passed.

## Runtime repair

The initial deployment built successfully but returned HTTP 500 because the backend tried to mount the `public/` directory after Vercel had promoted it to the CDN. A regression test reproduced the absent-directory condition. Hosted startup now leaves static assets to Vercel and redirects the backend homepage to the CDN index when needed; local static serving remains available.

## Boundaries

This verifies a real hosted sample chatbot, not the user's original PDF or a load/capacity guarantee. The original five-page local corpus and its evidence remain separate. Semantic caching is heuristic; cached page membership does not prove factual entailment. No dollar savings are fabricated. The Vercel and provider accounts may incur usage under their existing plans.

The existing OpenAI and Upstash credentials were copied into Vercel only after the user explicitly approved those credentials and that project as the destination. Their values were not printed, committed, bundled into browser assets or written into this report.
