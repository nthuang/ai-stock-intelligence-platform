# AI Stock Intelligence Platform

A reproducible stock analytics application that turns historical OHLCV data into
SQL-backed APIs, an interactive dashboard, threshold alerts, and optional
LLM-generated summaries.

**Live mode is the default.** Stock endpoints refresh from Yahoo Finance at most
once per 60 seconds, and a background task keeps the cache current while the
API is running. Configure a Groq key for generated summaries. Offline demo mode
is available explicitly with DATA_MODE=demo. The bundled dataset contains 620 rows across AAPL, AMZN, MSFT, NVDA, and
TSLA, covering 2025-10-27 through 2026-04-24. The application labels this snapshot
as historical/stale; it does not pretend to serve real-time prices.

## What this demonstrates

- Data engineering: per-ticker rolling indicators, validation, transactional
  PostgreSQL upserts, repeatable ingestion, and all-or-nothing ticker batches.
- Backend engineering: bounded parameterized SQL, pagination and date filters,
  documented response schemas, consistent empty lists, and safe error responses.
- Analytics: normalized performance charts, individual-stock moving averages,
  and an explicit, reproducible backtest with costs and compounding.
- Applied AI: factual summaries of rule-based signals, bounded provider retries,
  and a labeled fallback when the provider is unavailable.
- Delivery: non-root containers, readiness checks, offline fixtures, PostgreSQL
  integration tests, CI, and optional n8n notification automation.

Signals are deterministic rules: daily close-to-close returns above +3% or below
-3%. The LLM summarizes observations; it does not predict prices.

## Architecture

```mermaid
flowchart LR
    Y[Yahoo Finance: live mode] --> I[Validated ingestion]
    D[Bundled snapshot: demo mode] --> I
    I --> P[(PostgreSQL)]
    P --> A[FastAPI]
    A --> W[Interactive web dashboard]
    A --> B[Power BI / Tableau clients]
    A --> L[Optional Groq summary]
    L --> N[n8n: fresh signals only]
    N --> E[Gmail: configured by user]
```

## Run the application

Prerequisite: Docker Engine/Desktop with Compose. Copy .env.example to .env,
then configure GROQ_API_KEY. The default model is openai/gpt-oss-20b served by
Groq; set GROQ_MODEL to another model available to your account if needed.

```bash
docker compose up --build -d
```

Open:

- Dashboard: http://localhost:8000/dashboard
- API documentation: http://localhost:8000/docs
- Database/freshness status: http://localhost:8000/health

Startup waits for PostgreSQL, fetches live data, then starts the API. For an
offline snapshot, set DATA_MODE=demo before starting Compose.
Re-running ingestion updates the same date/ticker rows without duplicating them.
The first build needs network access to obtain images and packages; demo mode
does not call Yahoo Finance. Summary generation calls Groq only when a key is
configured and the summary button/endpoint is requested.

If ports are already occupied, set API_PORT and POSTGRES_PORT in your environment
or local environment file (for example 18000 and 15432). Services bind to loopback.

```bash
docker compose logs ingest api
docker compose stop
```

The database volume persists. Do not use volume-deleting commands if you want
to retain stored data.

## Configuration and live ingestion

Copy [.env.example](.env.example) to a local environment file and fill in optional
values. Never commit real credentials.

| Variable | Purpose |
| --- | --- |
| DATABASE_URL | Local Python PostgreSQL connection; Compose uses its internal hostname |
| GROQ_API_KEY | Optional; absent keys produce a labeled fallback |
| GROQ_MODEL | Summary model available to your provider account |
| ALERT_EMAIL | Optional recipient for the n8n Gmail node |
| MAX_DATA_AGE_DAYS | Freshness threshold in calendar days; default 4 |
| DATA_MODE | live (default) refreshes automatically; demo uses stored fixtures |
| REFRESH_INTERVAL_SECONDS | Live cache/background refresh interval; default 60, minimum 10 |

For live startup, keep DATA_MODE=live and run:

```bash
docker compose up --build -d
```

The API refreshes on startup and every REFRESH_INTERVAL_SECONDS (default 60).
Stock requests also refresh an expired cache before querying PostgreSQL;
concurrent requests share one refresh per API process. A recent successful fetch
is reused to avoid hitting Yahoo on every dashboard request. The browser reloads
data every minute without requiring a page refresh.

A failed refresh returns HTTP 503 on stock/summary endpoints instead of silently
returning old prices, with a short retry cooldown. /health exposes mode,
last_successful_fetch, last_refresh_attempt, and refresh_error. All five tickers
must download successfully with the same latest session before an update commits.
The API refresh never rewrites the bundled CSV fixtures.

Yahoo daily bars are not a real-time quote feed: the most recent bar may be the
previous session, delayed, or provisional during trading hours. Fetch time and
market-session date are shown separately. Run one API worker for this local
setup; multiple workers would each maintain their own refresh cache.

One-time refresh:

```bash
docker compose run --rm ingest python -m scripts.ingest --mode live
```

Do not reload demo mode into a live database: overlapping snapshot dates would
overwrite those rows. Use a separate Compose project for a distinct demo.

## Local Python development

Tested with Python 3.13 and PostgreSQL 17.

```bash
python -m venv .venv
# Activate the virtual environment using your shell.
python -m pip install -r requirements.txt
# Configure DATABASE_URL using .env.example, then:
python -m scripts.ingest --mode demo
uvicorn backend.main:app --reload
```

Run scripts as modules from the repository root.

## API contract

| Endpoint | Behavior |
| --- | --- |
| GET /health | Database connectivity, row count, latest date, age, and stale flag |
| GET /stocks | Newest-first rows; ticker, start/end dates, limit, offset |
| GET /tickers | Sorted distinct ticker symbols |
| GET /latest | Rows on the globally latest stored date |
| GET /signals | Paginated abnormal signals |
| GET /signals/latest | Abnormal signals on the latest date |
| GET /top-movers | Positive gainers and negative losers separately |
| GET /ai-summary/latest | Summary, provider source, model, date, stale flag, signals |
| GET /dashboard | Source-controlled interactive dashboard |

Example: /stocks?ticker=AAPL&start=2026-01-01&end=2026-04-24&limit=25&offset=0

Lists are ordered by date descending, then ticker. Pagination defaults to 50 and
is capped at 1,000 rows. Missing rolling indicators serialize as null. Empty
collection queries return []; latest-snapshot requests return 404 when no data
exists. Database failures return 503 without connection details. A connected but
stale database returns HTTP 200 with status=degraded: readiness and freshness
are deliberately separate.

The response field aliases retain dashboard-friendly names such as "Daily
Return %". This release changes signal endpoints to consistently return arrays,
and changes stock ordering to newest-first.

## Reproducible strategy experiment

```bash
python -m scripts.backtest_signals
```

The committed [metrics](data/processed/backtest_metrics.json) describe the bundled
snapshot, not live results:

| Metric | Result |
| --- | ---: |
| Execution sessions | 123 |
| Net strategy return | -0.6962% |
| Gross buy-and-hold benchmark | -3.1452% |
| Strategy maximum drawdown | -4.3023% |
| Annualized Sharpe, zero risk-free rate | -0.2165 |
| Completed signal trades | 85 |
| Net winning trades | 52.9412% |

Methodology:

1. Observe each ticker's signal after session t closes.
2. At the next session's open, allocate 1/N of current portfolio equity to that
   ticker's sleeve: long for BIG_GAIN, short for BIG_DROP, otherwise cash.
3. Close each position at that session's close. Deduct 5 basis points on entry
   and 5 on exit, each as a fixed fraction of the allocated sleeve.
4. Average sleeve returns, including idle sleeves, and compound daily portfolio
   returns. Include initial capital when calculating drawdown.
5. Compare against equal-dollar buy-and-hold from the first execution open to
   the final close over the same dates. The benchmark is gross of costs.

The panel must have complete common dates; duplicate or invalid prices fail
validation. Use --cost-bps to inspect sensitivity. Costs approximate fees and
slippage; short borrow, financing, margin constraints, and intraday liquidation
are not modeled. Prices are unadjusted and exclude dividends; corporate actions
can distort signals. Five selected surviving companies over six months are not
an out-of-sample evaluation. No evidence of a profitable predictive strategy is
claimed. In particular, this experiment changes the old same-close execution
assumption and replaces the invalid sum-of-trades return calculation.

## Tests and CI

```bash
python -m pytest -q
python -m compileall -q backend scripts tests
```

Unit tests use isolated SQLite fixtures and disable local credentials.
PostgreSQL-specific tests require TEST_DATABASE_URL pointing to a disposable
database. Each integration test creates and removes its own uniquely named
schema; it does not modify application tables.

With the demo stack running:

```bash
docker compose exec -e TEST_DATABASE_URL=postgresql://stock_user:stock_password@postgres:5432/stock_db api python -m pytest -q
```

CI provisions PostgreSQL, runs the complete suite, checks reproducible metrics,
validates Compose, builds and starts the demo, and checks it over HTTP. Tests cover null indicators, SQL
filters, pagination, empty datasets, stale data, error redaction, provider
success/failure, per-ticker indicators, incomplete ingestion, true upsert
idempotency, and backtest timing/allocation/costs.

## Optional n8n automation

```bash
docker compose --profile automation up -d --wait
docker compose exec n8n n8n import:workflow --input=/workflows/ai-stock.json
```

Open http://localhost:5678, complete local owner setup, and open the imported
workflow. Assign your own Gmail OAuth credential to the Gmail node. Configure
ALERT_EMAIL, review the recipient and message, test manually, then publish/activate
the workflow yourself. No credentials are included and the exported workflow is
inactive.

The schedule uses 18:00 America/New_York. The HTTP node calls
http://api:8000/ai-summary/latest inside Compose. Email is eligible only when
signals exist AND stale=false; historical demo data will not send alerts.
There is no delivery deduplication: repeated executions can resend a snapshot.
The API refreshes expired data before generating the summary; n8n does not run
a separate ingestion job.

The n8n image is pinned by digest. Environment access is enabled for this
single-user local workflow to resolve ALERT_EMAIL. Do not expose this development
configuration as a multi-user public service.

## Dashboards and demo walkthrough

The [web dashboard source](backend/static/dashboard.html) is included and runs
without a front-end build or third-party CDN. It normalizes prices to 100 for
cross-company comparisons and displays moving averages for a selected company.

A two-minute walkthrough:

1. Start the demo and open the dashboard; point out the historical-data label.
2. Compare normalized performance and switch the moving-average selector.
3. Inspect the alert table and generate a summary; show the provider/model and
   explain the explicit provider error if generation is unavailable.
4. Open API docs and demonstrate ticker filtering and pagination.
5. Show the real database upsert test and discuss backtest limitations.

Earlier BI explorations are preserved as screenshots:
[Power BI](dashboards/powerbi.png) and [Tableau](dashboards/tableau.png).
Their editable workbook files were not present in this repository. These images
are historical design references, not the canonical runnable dashboard.
The older [n8n screenshot](automation/n8n_workflow.png) also predates the freshness
gate; the exported JSON is authoritative.

## Operational boundaries

This is a local portfolio demo, not a production trading system. Public hosting
would require authentication, request/rate limits for paid summaries, TLS,
managed secrets, migrations, backups, and monitoring. Freshness currently uses
the globally latest date and calendar days, not per-ticker exchange calendars.
Existing databases use create_all initialization, not schema migrations. Direct
dependencies are pinned; transitive dependency resolution can still change.

The sample data is retained for reproducibility. Newly generated CSV/Parquet
files, environment secrets, test caches, and build outputs are ignored. Docker
build exclusions also prevent local secrets and virtual environments from
entering the build context.

## Summary troubleshooting

The summary uses all companies in the latest session, including normal movements;
zero threshold alerts do not disable AI generation. Response field provider_error
contains a safe explanation for missing credentials, unavailable models, quota,
or connection failures. When unavailable, the fallback lists observed price
changes and is explicitly labeled rule-based rather than AI-generated.

If a model returns NotFound, choose an ID available through your Groq account's
model list. After changing environment variables, recreate the API container with
`docker compose up -d --force-recreate api`. Never paste API keys into logs or
issue reports.
