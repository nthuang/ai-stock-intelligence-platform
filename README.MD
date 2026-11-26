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

Latest local verification: **147 tests passed with no skips**, including isolated PostgreSQL integration tests. One upstream Starlette/AnyIO deprecation warning remains. No Gmail message was sent.

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

1. **Resolve the provider/session mismatch.** The last checked provider fetch ended
   November 24 while legacy stored rows extend to November 25. Check `/health` and
   `/latest` for `latest_snapshot_observed_at` and `Observed At`. Do not assign a fetch
   timestamp to an old row unless the provider actually returned that price/session.
   The app intentionally withholds news for unobserved rows. Investigate provider
   timestamps and cached/download responses before changing ingestion dates.
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
AI selection, and 147 automated tests including PostgreSQL integration; added a
reproducible 60-case retrieval benchmark and 16 explanation-policy checks.
