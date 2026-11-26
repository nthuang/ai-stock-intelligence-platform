# Historical implementation notes

Archived implementation history. Commands, test counts, and limitations below describe earlier revisions. Use the root README for current setup and status. Relative links below originally resolved from the repository root.

# AI Stock Intelligence Platform

A reproducible stock analytics application that turns historical OHLCV data into
SQL-backed APIs, an interactive dashboard, threshold alerts, and optional
news-grounded research answers and optional LLM-generated summaries.

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
    A --> C[(PostgreSQL news cache and usage budgets)]
    C --> T[Tavily basic news search]
    C --> L[Optional Groq research and summary]
    L --> N[n8n: fresh signals only]
    N --> E[Gmail: configured by user]
```

## Run the application

Prerequisite: Docker Engine/Desktop with Compose. Copy .env.example to .env,
then configure GROQ_API_KEY. The default model is openai/gpt-oss-20b served by
Groq; set GROQ_MODEL to another model available to your account if needed.

```bash
docker compose -p stock-review up --build -d
```

Open:

- Dashboard: http://localhost:18000/dashboard
- API documentation: http://localhost:18000/docs
- Database/freshness status: http://localhost:18000/health

Startup waits for PostgreSQL, fetches live data, then starts the API. For an
offline snapshot, set DATA_MODE=demo before starting Compose.
Re-running ingestion updates the same date/ticker rows without duplicating them.
The first build needs network access to obtain images and packages; demo mode
does not call Yahoo Finance, Tavily, or Groq. Live research generation calls Groq only
on an explicit research/summary request, with a key configured and a cache miss.

If ports are already occupied, set API_PORT and POSTGRES_PORT in your environment
or local environment file (for example 18000 and 15432). Services bind to loopback.

```bash
docker compose -p stock-review logs ingest api
docker compose -p stock-review stop
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
docker compose -p stock-review up --build -d
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
docker compose -p stock-review run --rm ingest python -m scripts.ingest --mode live
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
| GET /ai-summary/latest | Cached market overview, all-stock performance, significant signals, eligible news sources, freshness and provider metadata |
| POST /ask | Bounded stock research; JSON body with question; numerical answers avoid news calls |
| POST /stock-news | Explicit news research for a ticker and displayed date; never substitutes another session |
| POST /notifications/claim | Fresh-signals-only daily delivery lease and escaped HTML report |
| POST /notifications/complete | Record successful Gmail delivery using the lease key/token |
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
docker compose -p stock-review exec -e TEST_DATABASE_URL=postgresql://stock_user:stock_password@postgres:5432/stock_db api python -m pytest -q
```

CI provisions PostgreSQL, runs the complete suite, checks reproducible metrics,
validates Compose, builds and starts the demo, and checks it over HTTP. Tests cover null indicators, SQL
filters, pagination, empty datasets, stale data, error redaction, provider
success/failure, per-ticker indicators, incomplete ingestion, true upsert
idempotency, and backtest timing/allocation/costs.

## Optional n8n automation

```bash
docker compose -p stock-review --profile automation up -d --wait
docker compose -p stock-review exec n8n n8n import:workflow --input=/workflows/ai-stock.json
```

Open http://localhost:5678, complete local owner setup, and open the imported
workflow. Assign your own Gmail OAuth credential to the Gmail node. Configure
ALERT_EMAIL, review the recipient and message, test manually, then publish/activate
the workflow yourself. No credentials are included and the exported workflow is
inactive.

The schedule uses 18:00 America/New_York. The HTTP node calls
http://api:8000/ai-summary/latest inside Compose. Email is eligible only when
signals exist AND stale=false; historical demo data will not send alerts.
PostgreSQL delivery leases prevent repeated daily reports for the same recipient;
only successful Gmail sends are acknowledged. See the update procedure below.
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
Research and notification endpoints are intended for this loopback-only local deployment; they have no authentication.
Existing databases use create_all initialization, not schema migrations. Direct
dependencies are pinned (including tavily-python 0.8.5); transitive dependency resolution can still change.

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
`docker compose -p stock-review up -d --force-recreate api`. Never paste API keys into logs or
issue reports.


## News intelligence implementation

The initial news helper is now connected to research and daily summaries. The
previously empty news cache and research modules implement persistent storage
using the existing SQLAlchemy engine; no extra database or service is required.
New tables are additive: news_cache, news_usage, research_cache, alert_deliveries.
Run database initialization when upgrading an existing installation.

### Free-tier configuration

Keep DATA_MODE=live to enable external retrieval. Set NEWS_ENABLED=true and add
your Tavily key in your local environment file. Configure a free Groq account and
GROQ_MODEL=openai/gpt-oss-20b or openai/gpt-oss-120b (subject to your account's
availability and free-tier limits). No paid API dependency was added. Tavily
searches explicitly use basic depth and disable automatic parameter upgrades.

| Variable | Default | Behavior |
| --- | --- | --- |
| NEWS_ENABLED | false | Explicit opt-in; DATA_MODE=demo overrides it |
| TAVILY_API_KEY | empty | Missing key returns a visible status without a request |
| NEWS_MAX_RESULTS | 5 | Maximum displayed articles per ticker, bounded to 1–20; one search checks 10–20 candidates |
| NEWS_CACHE_TTL_SECONDS | 21600 | Maximum cache lifetime; empty and intraday results expire within 15 minutes |
| NEWS_DAILY_SEARCH_LIMIT | 20 | UTC-day attempted search budget |
| NEWS_MONTHLY_SEARCH_LIMIT | 500 | UTC-month budget, capped at 900 |
| NEWS_MAX_TICKERS_PER_REQUEST | 2 | Maximum searched companies per research request, bounded to 1?5 |
| NEWS_TIMEOUT_SECONDS | 15 | Per-search timeout, bounded to 1?30 seconds |
| SUMMARY_CACHE_TTL_SECONDS | 21600 | Cache generated text for unchanged data/evidence/model |

The monthly cap leaves headroom below a 1,000-credit allowance; confirm your
account's actual allowance and lower it if necessary. Budgets cover this
application's basic-search attempts, not activity in other Tavily clients.
Attempts are reserved and committed before the network call, including failed
attempts. They survive restarts. PostgreSQL advisory locks serialize searches;
concurrent callers receive a busy status instead of duplicate external searches.
Cached results remain usable after a budget is reached. Cache storage failures
fail closed. Failed searches/generated fallbacks have a maximum 60-second
cooldown. Tavily calls have no automatic retry; a later explicit request may
retry after the cooldown and consumes another reservation. Groq permits one
bounded provider retry. Expired cache entries are replaced on access; there is
no background archival/pruning task.

Cache keys include ticker, market date, normalized intent, search depth, result
count and the strict same-session date policy. Legacy NEWS_LOOKBACK_DAYS values are ignored. Equivalent catalyst questions share searches;
earnings, dividend, acquisition and lawsuit intents have separate entries.
Daily summaries prioritize BIG_GAIN/BIG_DROP companies, then fill remaining
search slots with the largest ordinary moves. The stock refresh loop
never retrieves news or generates research. Dashboard research is requested by
buttons, independent of the existing minute-by-minute stock refresh.

### Grounding and historical boundaries

The router recognizes monitored tickers and company names. Pure numerical
questions return server-formatted database prices, daily returns, stored moving
averages, volatility and signals, without calling Tavily or Groq. Best/worst
comparisons rank stored daily returns. This is a bounded router, not a general
financial agent: it does not calculate arbitrary ratios or answer arbitrary
historical-date questions. All answers carry the actual stored session date.

Reason/catalyst/news questions combine those facts with eligible retrieved
articles. Model text is qualitative; generated numeric figures and unrecognized
citations cause a labeled fallback. Article identifiers are assigned by the
server; URLs, titles, publishers and publication dates come from retrieved
records. External article content is explicitly treated as untrusted data.
Reported events and possible influences are displayed separately; association
is not proof of causation. Validation checks citation membership and output
structure, not the factual truth of every provider article or model inference.

Stock and article dates must match in America/New_York. Retrieval searches that
single session date, and results are filtered again before being used in research.
The cutoff is the regular 16:00 New York close (converted with daylight-saving
time), capped at now for the current incomplete session. Prior-day, next-day and
after-cutoff articles are excluded, including cached articles with mismatched
dates. Date-only, missing or ambiguous timestamps cannot establish pre-cutoff
availability and are excluded. Special exchange early closes are not modeled;
this is not a point-in-time historical backtesting news dataset. Publication dates
and snippets depend on provider metadata. Daily bars may be delayed or provisional.

### Upgrade the existing stock-review environment

Do not overwrite your local environment file. Add the variables above as needed.
Your current host ports are API 18000, PostgreSQL 15432, and n8n 5678. Keep those
values and the stock-review project name so the existing volumes are reused.
From PowerShell at the repository root:

```powershell
$env:API_PORT = '18000'
$env:POSTGRES_PORT = '15432'
$env:N8N_PORT = '5678'
docker compose -p stock-review build api ingest
docker compose -p stock-review run --rm --no-deps api python -m backend.init_db
docker compose -p stock-review up -d --no-deps api
docker compose -p stock-review --profile automation up -d --no-deps n8n
docker compose -p stock-review ps
```

These steps add tables and recreate the API with the updated environment. They
do not reload demo data into your live database. PostgreSQL must already be
running (as in your existing setup). For a fresh installation, use
`docker compose -p stock-review --profile automation up --build -d --wait`.
Do not delete volumes; n8n_data contains your existing Gmail credential storage.
N8N_EDITOR_BASE_URL and WEBHOOK_URL continue to derive from N8N_PORT.

### Update the imported n8n workflow without losing Gmail authorization

1. In the existing n8n UI, download a backup of the current workflow and note its
   Gmail credential selection. Deactivate/unpublish the old scheduled workflow.
2. Import automation/n8n_workflow.json through the UI's **Import from File**
   command into a new workflow. A mounted JSON update alone does not update the
   workflow already stored in n8n. Leave the new workflow inactive while reviewing.
3. Open **Send a message** and select the same existing Gmail OAuth credential
   from the credential dropdown. Do not create a new credential or remove the
   existing n8n volume. The exported workflow intentionally contains no credential
   IDs, tokens or personal recipient address.
4. Check ALERT_EMAIL in your local environment. If changed, recreate only n8n
   with the command above so its environment updates. Keep its port stable; if
   changing it, update the Google OAuth allowed redirect to exactly the URL shown
   by n8n's credential dialog (normally
   http://localhost:5678/rest/oauth2-credential/callback).
5. Inspect **HTTP Request ? If ? Claim delivery ? Delivery allowed ? Send a message
   ? Record delivery**. The first If still requires fresh data and significant
   signals. The claim endpoint independently checks those conditions.
6. Execute the entire workflow manually with a fresh session containing a
   significant signal. If there is no eligible snapshot, it correctly sends
   nothing; do not bypass the freshness/signals gate to test historical data.
   Confirm a successful Gmail node, a successful Record delivery node, and the
   received HTML message. Then execute again: the claim should report already_sent
   and the Gmail node should not run. Publish only the new workflow afterward.

A failed send never reaches Record delivery. A pending claim expires after ten
minutes; rerunning the whole workflow can then retry. Record delivery itself has
three retries and is idempotent. Gmail sends are not automatically retried because
a transport timeout may occur after Gmail accepts a message. If Gmail succeeded
but acknowledgment failed, retry **Record delivery** from the saved execution
before rerunning the entire workflow. Exactly-once email delivery is not possible
across Gmail and PostgreSQL: an accepted send followed by a crash can still lead
to a duplicate after lease expiry. Only run one active daily workflow. Reports
are deduplicated per UTC market date and normalized recipient, even if prices
change later in that session.

### Example API requests

```powershell
Invoke-RestMethod http://localhost:18000/ai-summary/latest
Invoke-RestMethod http://localhost:18000/ask -Method Post -ContentType 'application/json' -Body '{"question":"What is NVDA latest closing price?"}'
Invoke-RestMethod http://localhost:18000/ask -Method Post -ContentType 'application/json' -Body '{"question":"Why did NVDA decline today?"}'
Invoke-RestMethod http://localhost:18000/ask -Method Post -ContentType 'application/json' -Body '{"question":"Compare Apple and Amazon performance and relevant news."}'
```

Other examples: Which stock performed best today? Which stocks triggered trading
signals? What is AAPL's 30-day moving average? What recent news may be influencing
Microsoft? Summarize today's market and explain significant movements.

A condensed illustrative summary response (values are examples, not a live quote):

```json
{
  "date": "2026-01-02",
  "summary": "AAPL: close $104.00, daily return +4.00%, signal BIG_GAIN. ...",
  "llm_source": "fallback",
  "model": "none",
  "stale": true,
  "signals": [],
  "performance": [],
  "market_overview": "No eligible news evidence is available.",
  "sources": [],
  "explanations": [],
  "news_status": "offline",
  "news_retrieval": {"AAPL": {"status": "offline", "cache_hit": false}},
  "tools_used": ["stock_data", "indicators", "trading_signals", "news_retrieval"],
  "generated_at": "2026-01-03T12:00:00+00:00",
  "analysis_cutoff": "2026-01-02T21:00:00+00:00",
  "cache_hit": false,
  "last_successful_fetch": null
}
```

The example omits stock records for brevity; actual performance and signals use
the existing StockRecord aliases. /ask adds answer and preserves the same
research metadata. A source contains id, ticker, title, url, publisher, published_date, news_date,
stock_date, timezone and relevance_score. news_date and stock_date must match. An explanation contains source_id,
reported_event and possible_influence. News statuses include ok, partial,
no_results, offline, disabled, missing_api_key, daily_limit, monthly_limit,
busy, request_limit, malformed_response, error and cache_unavailable.

### Verification and remaining checks

Verified locally on November 25, 2026: the final Python 3.13 Docker suite passed
125 tests with no skips, including PostgreSQL integration tests. One upstream
Starlette/AnyIO deprecation warning remains. The offline Docker build, HTTP
smoke checks, Python syntax checks, dashboard JavaScript syntax, and Compose
configuration validation passed. The backtest reproduced the committed metrics
without changes. Browser checks exercised the live dashboard's interpreted summary
and focused close-price answer. A live news question recognized "nvdia", corrected
an incorrect up/down premise, and returned two reports matching the stock session
and cutoff. Provider commentary that failed evidence validation fell back to
computed analysis and source links. No Gmail sends were made.

Automated tests mock Groq/Tavily and block unmocked provider calls. PostgreSQL
tests create and remove uniquely named test schemas, never application tables.
The implementation includes tests for persistent quotas, cache expiry, concurrent
requests, failures, article dates, question routing, citation validation,
summary cache invalidation, HTML escaping and notification state transitions.

```bash
python -m pytest -q
python -m compileall -q backend scripts tests
python -m scripts.backtest_signals
git diff --exit-code -- data/processed/backtest_metrics.json
# In your running stack, integration tests use temporary isolated schemas:
docker compose -p stock-review exec -e TEST_DATABASE_URL=postgresql://stock_user:stock_password@postgres:5432/stock_db api python -m pytest -q
```

Broader Tavily quality, model availability for both Groq IDs, and actual delivery
through the updated imported Gmail workflow require account-specific checks.
No live AI credits or Gmail messages are needed for the automated suite. Browser
and SMTP/Gmail provider behavior are separate from unit-level HTML safety tests.


### Dashboard analysis and stock news

Generate summary interprets the stored snapshot using computed participation,
equal-weight and median returns, leader/laggard dispersion, moving-average gaps,
open-to-close direction and the size of a move relative to seven-session return
variability. It does not concatenate every quote with another daily-return list.
All calculations and numerical interpretations run in the backend; optional AI
prioritizes verified observations and adds validated, source-linked news context.
It cannot replace computed comparisons with free-form technical claims.
A missing AI key or offline mode still provides useful numeric
analysis. Equal-weight performance describes the selected sample, not a market index.

The dashboard keeps the numerical overview and replaces the general question box
with **News behind the move**. Select a company and choose **Find news for this
move** to search its displayed session. The request sends `ticker` and `date` to
`POST /stock-news`; missing prices return 404 rather than substituting another
company or date. Company trend insights also link directly to this action.

Reports are ranked by a transparent headline heuristic: company events first,
then company coverage, opinion/comparison pieces, and broader market context.
Question-style opinion headlines and retrospective holdings reports are labeled
separately. Holdings reports remain available as sources but are excluded from
generated move explanations: publication today does not prove trades happened
today. The final ranking/citation/cache suite passed 77 focused tests after this
safeguard was added.
Unrelated candidates are excluded. These labels describe relevance, not proof
of an event or price causation. Provider relevance and recency break ties.
Tracking parameters are removed when deduplicating article URLs; distinct URLs
for syndicated copies can still appear separately.

Reports appear as cards with the original title, publisher, New York publication
time and source link. Each validated possible connection stays beside its source.
Cards separate **What was reported** (the original source headline) from
**Why it might matter** (an optional validated AI interpretation). Missing
interpretations are explicitly labeled, and the model receives coverage labels
and is told not to treat opinion or broader context as company announcements.
Search queries use company/ticker names and optional recognized event intents;
dates are supplied as filters rather than extra query terms. Tavily publication
filtering is enabled as documented in its
[Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search),
and the server independently checks the New York session date and cutoff.

If Tavily succeeds but returns no eligible articles, one public Yahoo company RSS
request provides a fallback. It is limited to eight seconds, 512 KB and 50 feed
items, and keeps only items mentioning the company or ticker. The same date,
cutoff, URL and article checks apply. Quote pages are excluded because their fresh
page timestamp can accompany older headlines. Both providers share the persistent
result cache. The fallback does not add a paid search or bypass disabled news,
missing-key or exhausted-budget states. RSS availability is not guaranteed and
historical coverage is limited to items still in the feed.

Source links remain visible if AI commentary is unavailable or fails validation;
one rejected explanation cannot discard other valid explanations. No eligible
articles, disabled news and budget limits have separate visible states. Empty
and intraday results expire within 15 minutes so later explicit requests can
find new reports; automatic stock refresh does not consume news credits.

The November 25 live check returned one NVDA candidate without a precise
publication timestamp, so it was correctly excluded. The dashboard displays
that reason and the cache expiry; retrieval improvements do not guarantee that
the provider supplies eligible same-session news. Browser fixture checks also
verified source cards, attached explanations, unsafe-link rejection and rejection
of mismatched session dates. The final cache changes passed 19 focused tests,
and the dashboard JavaScript syntax and offline HTTP smoke checks passed.

A subsequent live check after enabling the RSS fallback found seven eligible
NVDA reports for November 25, displayed five source cards and returned four
validated possible connections. Ten search results from other sessions were
excluded. Automated coverage now includes feed parsing, company matching,
quote-page rejection, request headers, oversized payloads and malformed XML.

The subsequent relevance evaluation checked all five companies through the live
API on November 25: AAPL returned five articles, AMZN two, MSFT five, NVDA one,
and TSLA one. Every source matched its stock-session date. Validated explanation
counts were three, zero, two, zero, and one respectively; missing commentary did
not hide sources. These are point-in-time observations, not guaranteed coverage.

The `/ask` API remains available for compatibility and focused numerical queries,
including common Nvidia misspellings and corrections of wrong up/down premises.
Stock session dates remain separate from analysis and news retrieval timestamps.

The offline preview on port 18001 intentionally disables Tavily and Groq. Its
news-unavailable message does not mean numerical analysis failed. The live
stock-review deployment is on port 18000 and uses its own existing configuration.
