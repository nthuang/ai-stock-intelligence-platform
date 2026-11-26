# News evaluation protocol

## Reproduce the offline policy benchmark

Run `python -m scripts.evaluate_news --check --output data/processed/news_evaluation.json`.
Fixtures are authored synthetic examples in [news_evaluation.json](../tests/fixtures/news_evaluation.json).
The command replaces provider boundaries with fixed responses; no provider credentials or network calls are needed.

Twelve retrieval scenarios are crossed with five monitored tickers: eligible events,
prior-session news, post-close news, date-only and timezone-less timestamps, UTC/New York
date mismatch, quote pages, unrelated companies, opinion, old holdings reports, unsafe URLs,
and broad market context. Precision and recall describe these fixtures only.

The original ten probes exposed two accepted invented claims about a lawsuit and an
acquisition. The redesigned path accepts only a source ID, an exact excerpt quote, and
one of eight supported topics. The server verifies excerpt membership and topic matching;
it never displays model-written interpretation prose. Conditional server templates explain
why that category of coverage can matter without asserting a new event or price causation.

The current 16 probes include the original failures, invented quotes, cross-source quotes,
topic mismatches, and supported denial/earnings contexts. All meet their expectations;
zero of the 13 unsupported probes are accepted. The report preserves input selections
and actual displayed explanations. `--check` gates policy regressions, **not real-world
factual accuracy**. Source reports may be false, context may be incomplete, and the topic
matcher is a heuristic. Do not advertise fixture precision as model factual accuracy.

## Real-world evaluation still required

Collect at least 50 ticker/session requests over multiple sessions, including quiet days,
large moves, unavailable providers, empty news results, and contradictory reports. Keep
the exact retrieved title/excerpt, publication timestamp, fetch boundary, generated claim,
model/prompt version, and response hash. Prefer public links plus bounded excerpts over
redistributing copyrighted full articles. Do not cherry-pick requests that returned sources.

Review each claim against its cited excerpt, without treating a citation or the model's own
confidence as evidence. Assign one of: supported, unsupported, contradicted, insufficient
evidence. Record whether the source addresses the company, whether the described event
occurred in the session or is retrospective, and whether the explanation overstates causality.
Have a second reviewer check a subset and report disagreements.

Report separate denominators:

- Coverage: requests with at least one eligible source / all requests.
- Relevance: relevant displayed sources / all reviewed displayed sources.
- Claim support: supported claims / all reviewed claims, retaining insufficient-evidence cases.
- Date compliance: displayed sources within the requested session and cutoff / all displayed sources.
- Abstention: requests with no generated explanation / all requests.
- Operational behavior: median and p95 latency, cache-hit rate, reserved searches per request,
  and failures, separated by cached/uncached and numerical/news paths.

Compare the existing model output with a source-headlines-only baseline using the same
frozen evidence. Do not use an LLM judge as the sole ground truth. Predetermine release
thresholds before scoring; a tiny sample with no observed errors is not proof of zero risk.

## Timing limits

NASDAQ session boundaries come from the pinned calendar package, including scheduled
early closes and holidays. Calendars need updates for newly announced closures. A persisted
fetch-start time is an application observation boundary, not a provider quote timestamp.
Daily bars may be delayed, and historical news metadata may be revised. These controls
reduce mismatches but do not make this a point-in-time causal or backtesting news dataset.
