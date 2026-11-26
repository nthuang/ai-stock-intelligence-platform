"""Deterministic interpretation of stored indicators, independent of news/LLMs."""
import math
from statistics import median


def number(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def stock_analysis(row):
    ticker = row['Ticker']
    close, opening = number(row.get('Close')), number(row.get('Open'))
    change, volatility = number(row.get('Daily Return %')), number(row.get('Volatility_7'))
    gaps = {period: (close / average - 1) * 100 if close is not None and average and average > 0 else None
            for period in (7, 30) for average in [number(row.get(f'MA_{period}'))]}
    intraday = (close / opening - 1) * 100 if close is not None and opening and opening > 0 else None
    ratio = abs(change) / volatility if change is not None and volatility and volatility > 0 else None
    details = []
    if gaps[7] is not None and gaps[30] is not None:
        position = f"{abs(gaps[7]):.2f}% {'above' if gaps[7] >= 0 else 'below'} its 7-session average and {abs(gaps[30]):.2f}% {'above' if gaps[30] >= 0 else 'below'} its 30-session average"
        if gaps[7] > 0 and gaps[30] > 0:
            meaning = "the stored price remains above both recent trend baselines"
        elif gaps[7] < 0 and gaps[30] < 0:
            meaning = "the stored price remains below both recent trend baselines"
        else:
            meaning = "the short and longer trend baselines give mixed readings"
        details.append(f"{ticker} is {position}; {meaning}.")
        if change is not None and change > 0 and gaps[30] < 0:
            details.append("The daily gain is a rebound within a still-weaker longer trend, not confirmation of a recovery.")
        elif change is not None and change < 0 and gaps[30] > 0:
            details.append("The daily decline is a pullback while the price remains above its longer trend baseline.")
    else:
        details.append(f"{ticker} has insufficient moving-average history to assess trend alignment.")
    if intraday is not None and change is not None and change != 0:
        if intraday * change < 0:
            details.append(f"Its {intraday:+.2f}% open-to-close move opposed the daily return; the opening gap and trading after the open pulled in different directions.")
        else:
            details.append(f"Its {intraday:+.2f}% open-to-close move {'reinforced' if intraday else 'did not extend'} the daily direction.")
    if ratio is not None and ratio >= 2:
        details.append(f"The daily move was {ratio:.2f} times its seven-session return standard deviation, a large move relative to that recent variability (which includes this session).")
    quote = (f"{ticker} stored price: ${close:.2f}" if close is not None else ticker)
    if change is not None:
        quote += f", {'up' if change > 0 else 'down' if change < 0 else 'unchanged at'} {abs(change):.2f}% for the session"
    return {'ticker': ticker, 'quote': quote + '.', 'interpretation': ' '.join(details),
            'gap_ma_7_pct': gaps[7], 'gap_ma_30_pct': gaps[30],
            'intraday_return_pct': intraday, 'move_to_volatility_ratio': ratio}


def analyze_snapshot(rows):
    valid = [row for row in rows if number(row.get('Daily Return %')) is not None]
    stocks = [stock_analysis(row) for row in rows]
    if not valid:
        return {'overview': 'Daily returns are unavailable, so participation and relative performance cannot yet be assessed.',
                'observations': [], 'stocks': stocks, 'metrics': {}}
    returns = [row['Daily Return %'] for row in valid]
    up, down = sum(value > 0 for value in returns), sum(value < 0 for value in returns)
    average, middle = sum(returns) / len(returns), median(returns)
    if up == len(valid):
        direction = 'Gains were broad across the selected stocks.'
    elif down == len(valid):
        direction = 'Declines were broad across the selected stocks.'
    elif up > down:
        direction = 'Gains outnumbered declines, but participation was not unanimous.'
    elif down > up:
        direction = 'Declines outnumbered gains, but participation was not unanimous.'
    else:
        direction = 'The selected stocks were evenly split or flat; there was no shared direction.'
    overview = direction + ' This describes the monitored stocks, not a broad-market index.'
    observations = [{'title': 'Participation', 'text': f"{up} of {len(valid)} stocks advanced, {down} declined and {len(valid)-up-down} were unchanged. "
                     f"The equal-weight daily return was {average:+.2f}%, versus a median of {middle:+.2f}%; "
                     + ('the average was lifted above the middle stock by stronger performers.' if average > middle + .01 else
                        'the average was pulled below the middle stock by weaker performers.' if average < middle - .01 else
                        'the average and middle stock moved similarly.')}]
    best, worst = max(valid, key=lambda row: row['Daily Return %']), min(valid, key=lambda row: row['Daily Return %'])
    spread = best['Daily Return %'] - worst['Daily Return %']
    if len(valid) > 1:
        observations.append({'title': 'Leadership and dispersion', 'text':
            f"{best['Ticker']} led and {worst['Ticker']} lagged, separated by {spread:.2f} percentage points. "
            + ('The positive average therefore masks losses in part of the group.' if average > 0 and down else
               'The negative average therefore masks gains in part of the group.' if average < 0 and up else
               'A near-flat group average masks opposing stock moves.' if abs(average) < .01 and up and down else
               'The spread shows how differently individual stocks performed within the group.')})
    known = [stock for stock in stocks if stock['gap_ma_30_pct'] is not None]
    above = sum(stock['gap_ma_30_pct'] > 0 for stock in known)
    if known:
        observations.append({'title': 'Trend context', 'text':
            f"{above} of {len(known)} stocks with enough history are above their 30-session average at the stored price. "
            'This separates the current trend position from a single day of gains or losses.'})
    else:
        observations.append({'title': 'Trend context', 'text': 'There is not enough stored moving-average history to confirm whether the daily moves align with longer trends.'})
    alerts = [row['Ticker'] for row in valid if row.get('Signal') in {'BIG_GAIN', 'BIG_DROP'}]
    observations.append({'title': 'Movement alerts', 'text':
        (f"{', '.join(alerts)} crossed the configured 3% movement threshold. These flag moves worth investigating; they do not explain the cause or predict the next session."
         if alerts else 'No stock crossed the configured 3% movement threshold; ordinary moves can still matter in the context of the longer trend.')})
    return {'overview': overview, 'observations': observations, 'stocks': stocks,
            'metrics': {'advancers': up, 'decliners': down, 'unchanged': len(valid)-up-down,
                        'return_count': len(valid), 'missing_returns': len(rows)-len(valid),
                        'equal_weight_return_pct': average, 'median_return_pct': middle,
                        'leader': best['Ticker'], 'laggard': worst['Ticker'], 'spread_percentage_points': spread,
                        'above_ma_30': above, 'ma_30_count': len(known)}}


def analysis_text(analysis):
    return ' '.join([analysis['overview'], *(item['text'] for item in analysis['observations'])])
