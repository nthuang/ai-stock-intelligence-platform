# Market Observatory

A stock research platform that connects numerical analysis with dated news evidence. Built with Python, FastAPI, PostgreSQL, and a browser dashboard. It monitors AAPL, AMZN, MSFT, NVDA, and TSLA; it does not predict prices or claim a profitable trading strategy.

## Try it without API keys

Install Docker Desktop with Compose 2.24.4 or newer, then run from the repository root:

```bash
docker compose -p portfolio-demo --env-file .env.example -f docker-compose.yml -f docker-compose.demo.yml up --build -d --wait api
```

Open [the dashboard](http://localhost:18002/dashboard) or [API docs](http://localhost:18002/docs). This isolated demo loads 620 historical rows, disables every external AI/news provider, removes the database host port, and disables notifications. It uses its own database volume. It remains bound to localhost; this command does not publish a website.

The historical/stale label is intentional. Generate a summary to see computed interpretation even without an AI provider. News searches correctly report that the demo is offline.

![Dashboard with computed market analysis](docs/dashboard-demo.png)

## What makes this more than an API wrapper

- **Numerical interpretation:** participation, average versus median returns, leadership dispersion, moving-average gaps, and move size relative to recent volatility are computed in Python. AI cannot rewrite those calculations.
- **Evidence boundaries:** news must match the stock's New York session and precede the exchange close and stored fetch boundary. Holidays, daylight saving, and scheduled early closes use a NASDAQ calendar.
- **Constrained interpretation:** AI selects a source excerpt and topic. The server verifies that the quote occurs in that source and that the topic matches, then supplies conditional context from a fixed set of templates. Free-form model claims are never displayed.
- **Controlled retrieval:** bounded Tavily search, Yahoo RSS fallback, article filtering, coverage labels, persistent caches, and transactionally reserved search budgets.
- **Reliable storage:** validated per-company rolling indicators, all-or-nothing live batches, PostgreSQL upserts, and fetch timestamps committed with prices.
- **Honest failure behavior:** missing sources, exhausted budgets, unavailable models, and stale data are visible states. Source links remain useful without generated commentary.

```mermaid
flowchart LR
    Yahoo[Yahoo daily bars] --> Ingest[Validate and compute indicators]
    CSV[Offline fixture] --> Ingest
    Ingest --> DB[(PostgreSQL prices and fetch boundaries)]
    DB --> API[FastAPI]
    API --> Dashboard[Dashboard]
    API --> Cache[(News cache and persistent budgets)]
    Cache --> Tavily[Tavily basic search]
    Tavily --> RSS[Yahoo RSS fallback when no eligible reports]
    Cache --> Filter[Session and relevance checks]
    Filter --> Groq[Optional Groq interpretation]
    Groq --> API
    API --> Workflow[n8n claim / send / acknowledge]
    Workflow --> Gmail[Gmail configured by owner]
```

## Verification and measured limits

Run in a configured Python 3.13 environment:

```bash
python -m pytest -q
python -m scripts.evaluate_news --check --output data/processed/news_evaluation.json
python -m scripts.backtest_signals
```

PostgreSQL tests require `TEST_DATABASE_URL`; each uses its own temporary schema. The [CI workflow](.github/workflows/ci.yml) runs tests, reproduces the backtest, uploads the news evaluation report, and smoke-tests the offline API. A workflow definition is not a claim that the latest hosted run is green.

The [news evaluation report](data/processed/news_evaluation.json) contains **60 synthetic retrieval cases**: 12 scenarios across five tickers. All currently meet their expected filtering/coverage policy. These are not 60 independent real-world news samples.

Latest local verification: **156 tests passed with no skips**, including isolated PostgreSQL integration tests and exact committed-backtest reproduction. Two upstream TestClient/AnyIO deprecation warnings remain. The patched Python environment passed pip-audit with no known vulnerabilities. This does not cover OS packages or prove absence of vulnerabilities. No Gmail message was sent.

The updated demo passed HTTP smoke checks and browser rendering checks, including
displaying an HTML-like excerpt as inert text. One bounded request to the configured
Groq model also returned a valid topic and exact excerpt for a clearly synthetic input.
That checks the provider contract; it is not a real-news accuracy evaluation.

The original benchmark exposed two unsupported claims that passed citation/wording checks. The redesigned path rejects both, with **16 of 16 authored explanation checks passing and zero of 13 unsupported probes accepted**. It also rejects fabricated excerpts and unsupported topic choices. These fixture results do not measure live-model factual accuracy: source reports can be wrong, quotes can omit context, and a shared date does not establish causation. The dashboard shows the selected excerpt and labels the explanation as general topic context. See the [evaluation protocol](docs/evaluation.md).

The [backtest](data/processed/backtest_metrics.json) returns **-0.6962% net**, with **-0.2165 Sharpe**, across 123 execution sessions and 85 trades. Signals use the prior close, enter at the next open, exit at that close, and deduct five basis points per side. This small, selected five-stock dataset is an engineering experiment, not evidence of a trading edge. The buy-and-hold comparison is gross of costs; dividends, borrow costs, and corporate-action adjustments are not modeled.

## Local live mode

Copy [.env.example](.env.example) to `.env`, preserving any existing local configuration. Set `DATA_MODE=live`; optionally configure `GROQ_API_KEY`, `NEWS_ENABLED=true`, and `TAVILY_API_KEY`. Provider use remains subject to your account's limits. The default application search limits are 20 attempts per UTC day and 500 per UTC month.

```bash
docker compose -p stock-review up --build -d --wait api
```

Open [the live dashboard](http://localhost:18000/dashboard). Live ingestion refreshes at most once per minute per API process. Run one API worker. Prices are daily bars and may be delayed or provisional; fetch time is not an exchange quote timestamp. Never load demo data into the live project's database.

For an existing installation, add the new observation table before restarting the API, without reloading fixtures:

```bash
docker compose -p stock-review build api ingest
docker compose -p stock-review run --rm --no-deps api python -m backend.init_db
docker compose -p stock-review up -d --no-deps api
```

This release adds tables without changing existing columns. A general schema migration framework is still pending.

Older rows have no observation timestamp until they are fetched again. If the provider returns an older session than the latest stored row, the app retains that row but marks its timing as unknown and withholds news explanations. It never assigns the new fetch time to prices that were not returned.

## API and operational boundaries

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Connectivity, stored date, stale state, and refresh status |
| `GET /system/status` | Exchange state, ingestion duration, persisted news budgets, delivery records, and process counters; no external requests |
| `GET /stocks`, `/latest`, `/tickers` | Bounded stock queries and displayed universe |
| `GET /signals`, `/signals/latest`, `/top-movers` | Computed alerts and rankings |
| `GET /ai-summary/latest` | Computed interpretation and optional news context |
| `POST /stock-news` | News for the exact requested ticker and stored date |
| `POST /ask` | Compatibility endpoint for bounded research questions |
| `POST /notifications/claim`, `/notifications/complete` | Private delivery coordination; requires `X-Notification-Key` |

The [demo Compose override](docker-compose.demo.yml) forces offline mode and clears provider keys. Demo traffic is capped at 30 requests per minute per client and 600 globally per process; health checks are excluded. Behind a proxy clients share its limit unless trusted proxy handling is deliberately configured. Request logs contain route templates, HTTP status, and elapsed time, not prompts or credentials. News responses expose cache status and retrieval diagnostics.

Public live hosting is not supported by this configuration. A hosted offline demo still needs a hosting account, TLS/reverse proxy, and an agreed URL. Local live endpoints rely on loopback isolation. Multi-user paid access, distributed rate limiting, deployment automation, backups, and alerting remain future work.

## Notification automation

The [inactive n8n workflow](automation/n8n_workflow.json) schedules reports at 18:00 New York time. Configure `NOTIFICATION_API_KEY` with a long random value shared by API and n8n, set `ALERT_EMAIL`, and select your Gmail OAuth credential in n8n. Internal notification endpoints deny requests when the key is missing. The public demo disables them regardless of key.

The API requires a closed session, fresh data, and significant signals before granting a delivery lease. Tests cover the HTTP claim/render/acknowledge path, pending leases, expired-lease retries, and duplicate suppression. **Actual Gmail delivery and scheduled n8n execution have not been verified.** An accepted email followed by an acknowledgment failure can still duplicate after lease expiry; do not claim exactly-once delivery.

Before enabling a schedule: import the updated workflow, leave it inactive, review its recipient and generated report, then perform an explicitly approved test send. Preserve the existing n8n volume and Gmail credentials.

## Two-minute walkthrough

1. Open the dashboard and identify the historical/demo label.
2. Compare the normalized chart and one company's trend interpretation.
3. Generate Daily Market Intelligence and explain one computed observation.
4. Show the news policy evaluation and explain how the two previously accepted invented claims are now rejected.
5. Explain how transactions, cache budgets, and source timestamps constrain failures.

For a resume: **Built a news-grounded stock research platform with FastAPI, PostgreSQL, transactional ingestion, persistent retrieval budgets, and reproducible evaluation.** Add measured latency, live evaluation results, and a hosted demo link only after verifying them.

Earlier implementation notes and point-in-time checks are preserved in [implementation history](docs/implementation-history.md). They are archival; this README describes the current setup.

## Next-session handoff

The local live stack is `stock-review` on port 18000. The isolated portfolio demo is
`portfolio-demo` on port 18002. An older offline stack may still be running on port
18001; it is not the current portfolio demo. Keep the live database and n8n volumes.
All current changes are local and uncommitted; review the diff before creating a commit.

Recommended order of work:

1. **Monitor provider/session alignment.** The latest read-only live check showed a
   November 25 snapshot with a matching observation timestamp; the earlier mismatch
   was no longer present. Continue checking `/health` and `/latest`. Never assign a
   fetch timestamp to a row unless the provider returned that price/session.
2. **Measure real retrieval and evidence selection.** Follow the evaluation protocol
   across multiple sessions. The offline benchmark is complete; live provider quality,
   selection coverage, and human-reviewed source relevance are not measured yet.
   No extra LLM verification service is required by the current design.
3. **Choose a host and publish the offline demo.** The demo Compose configuration is
   ready locally. A public URL, TLS configuration, and hosted CI result are still pending.
   Keep the live paid-provider setup private until authenticated access and distributed
   limits have been designed.
4. **Verify one real notification, then enable scheduling.** Configure the shared
   notification key, import the inactive workflow, preserve existing Gmail credentials,
   and review the recipient/report before an explicitly approved send. Automated tests
   do not prove delivery through Gmail or n8n.
5. **Record the short walkthrough and prepare interview explanations.** Explain the
   original unsupported-claim failure, why the model now selects evidence instead of
   writing claims, how search reservations survive crashes, and the remaining timestamp
   and backtest limitations. Use the README's measured facts rather than a '10/10' claim.

Suggested resume wording: Built a five-stock research platform using FastAPI and
PostgreSQL, with transactional ingestion, persistent news-search budgets, exact-excerpt
AI selection, and 156 automated tests including PostgreSQL integration; added a
reproducible 60-case retrieval benchmark and 16 explanation-policy checks.

## Production-readiness review

The bounded reliability pass preserves the financial methodology and existing PostgreSQL/n8n volumes. No environment secrets, OAuth credentials, or recipients were changed. No model comparison or new benchmark was added.

Confirmed findings and fixes:

| Priority | Finding | Resolution |
| --- | --- | --- |
| High | Live CLI ingestion overwrote the committed backtest/demo fixture before database commit | Export to a separate ignored live CSV, after successful commit; reject inconsistent latest sessions |
| High | A delayed overlapping fetch could overwrite newer prices and observation times | PostgreSQL transaction advisory lock plus rejection of older observed batches; concurrent-writer regression test |
| High | Refresh timestamps invalidated unchanged generated summaries | Cache identity excludes fetch timestamps after evidence eligibility filtering; changed values or sources still invalidate |
| High | Installed dependencies had published advisories | Update affected package chains and verify build, advisory scan, tests, and HTTP behavior; an affected version alone does not prove application exploitability |
| Medium | All-hours refresh every minute and calendar-day freshness | NASDAQ session-aware cadence, 30-minute close grace, hourly closed-market polling, freshness against latest completed session |
| Medium | Weekend automation could claim the preceding session's report | Notification claim requires today's New York trading date as well as a closed, fresh snapshot and signals |
| Medium | Missing operational aggregate visibility | Read-only status endpoint, bounded process counters and JSON event logs, dashboard status details |
| Medium | Generation output lacked an explicit token cap | Cap completion tokens at 2,048 for numerical focus and 4,096 for research; at most ten excerpts enter research generation |
| Low | Non-ASCII notification secret could raise an internal error during comparison | Compare UTF-8 bytes with constant-time comparison |

No critical application vulnerability was confirmed in this review. SQL uses bound parameters; existing date/ticker uniqueness and indexes support the observed query patterns. No additional index was justified by a measured bottleneck. News budgets remain persistent and fail closed; citation validation, exact excerpt checks, and escaped HTML rendering remain intact.

Operational semantics:

- `/health` and `/system/status` read database/process/calendar state only. They never download quotes, retrieve news, generate AI text, or send mail.
- News quota counts are persistent UTC daily/monthly reservations. Process request, cache and LLM counters reset on restart and describe this API worker only. LLM counts measure logical calls; SDK retries can cause additional attempts. HTTP latency totals cover requests reaching the application handler, not early access denials.
- Delivery records describe claims and acknowledged sends, not confirmed inbox delivery. Gmail acceptance followed by acknowledgment failure can still duplicate after lease expiry.
- `REFRESH_INTERVAL_SECONDS` applies during the exchange session and closing grace period. Closed-market checks occur at most hourly, including retries. `MAX_DATA_AGE_DAYS` is superseded by exchange-session freshness; the old environment value can remain without effect.
- Startup still attempts a refresh. Full six-month downloads remain to preserve rolling-feature calculations and reconcile historical revisions; incremental download windows are deferred. Upserts are idempotent, but this is not yet incremental network ingestion.
- Provider outages preserve committed data; live data endpoints still return an explicit 503 while the status endpoint reports the failure. No failure is silently presented as fresh data.

Remaining work: monitor provider/session alignment; verify one explicitly approved Gmail send and actual imported n8n execution; add backup/restore verification and a transitive dependency lock; supply editable Power BI/Tableau workbooks (only screenshots are present). Expired cache rows are retained and need a small retention policy before long-term operation. Hosted CI results and OS-image vulnerability scanning are not established by local tests. Browser automation was unavailable during this pass; HTTP dashboard delivery passed but the new status disclosure was not visually inspected. A public deployment remains out of scope.

Local verification (PowerShell, repository root; Docker Desktop running):

```powershell
docker build -t stock-readiness-check .
docker compose -p stock-review --profile automation config --quiet
docker run --rm --network stock-news-check_default --mount "type=bind,source=$PWD,target=/app,readonly" -e PYTHON_DOTENV_DISABLED=1 -e TEST_DATABASE_URL=postgresql://stock_user:stock_password@postgres:5432/stock_db stock-readiness-check python -m pytest -q -p no:cacheprovider
docker compose -p portfolio-demo --env-file .env.example -f docker-compose.yml -f docker-compose.demo.yml up --build -d --wait api
docker exec portfolio-demo-api-1 python -m scripts.smoke_test --base-url http://localhost:8000
Invoke-RestMethod http://localhost:18002/system/status
```

The integration command uses the existing local test stack and creates/drops unique test schemas only. On another machine, configure `TEST_DATABASE_URL` to a disposable PostgreSQL instance. To apply a verified build to the existing live API without restarting n8n or running ingestion as a dependency:

```powershell
docker compose -p stock-review build api ingest
docker compose -p stock-review up -d --no-deps --wait api
```

No new environment setting or database migration is required. Never remove the existing volumes as part of these commands.

Validation outcome for this pass: 156 tests passed, zero skipped (including PostgreSQL concurrent writers and backtest equality); Compose configuration and offline HTTP smoke checks passed; `pip check` found no broken requirements and pip-audit found no known vulnerabilities in the patched Python packages. Two upstream test-client deprecation warnings remain. The updated local `stock-review` API is healthy, retains 1,265 rows, and reports the November 25 session with a matching persisted observation. One startup ingestion took 2.223 seconds on this local Docker installation; this is a single observation, not a performance benchmark or speedup claim. No Tavily/Groq request or real email was used in this pass. The automation service was not started.

Files changed:

- Runtime: [access](backend/access.py), [AI summary](backend/ai_summary.py), [API](backend/main.py), [calendar](backend/market_calendar.py), [news cache](backend/news_cache.py), [refresh](backend/refresh.py), [research](backend/research.py), and new [operational counters](backend/operations.py).
- Data and UI: [ingestion](scripts/fetch_data.py), [smoke checks](scripts/smoke_test.py), [dashboard](backend/static/dashboard.html).
- Verification: [access tests](tests/test_access.py), [API tests](tests/test_api.py), [backtest tests](tests/test_backtest.py), [PostgreSQL tests](tests/test_database_integration.py), [ingestion tests](tests/test_fetch_data.py), [calendar tests](tests/test_market_calendar.py), [refresh tests](tests/test_refresh.py).
- Build/documentation: [Dockerfile](Dockerfile), [requirements](requirements.txt), and this README.

Recommended next phase: prove recovery with a disposable backup/restore drill, add bounded cache retention and a transitive dependency lock, then finish the interview walkthrough and editable BI deliverables. Keep actual email delivery as a separately approved manual verification. Suggested commit message: `fix: harden ingestion ordering, research caching, and operational readiness`.
