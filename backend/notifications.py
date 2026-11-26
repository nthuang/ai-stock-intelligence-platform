"""Escaped HTML reports and persistent daily delivery leases."""
from html import escape
import secrets
import time

from sqlalchemy import select

from backend.models import AlertDelivery
from backend.news import safe_url
from backend.news_cache import digest, exclusive


def render_report(summary):
    def e(value):
        return escape(str(value if value is not None else "Unavailable"), quote=True)

    rows = "".join("<tr>" + "".join(f"<td style='padding:8px;border-bottom:1px solid #ddd'>{e(value)}</td>" for value in
                       (row["Ticker"], f"${row['Close']:.2f}",
                        f"{row['Daily Return %']:+.2f}%" if row.get("Daily Return %") is not None else "Unavailable",
                        row.get("Signal") or "WARMUP")) + "</tr>" for row in summary.get("performance", []))
    links = []
    for source in summary.get("sources", []):
        url = safe_url(source.get("url"))
        if url:
            links.append(f"<li>[{e(source['id'])}] <a href='{e(url)}'>{e(source['title'])}</a> — "
                         f"{e(source.get('publisher'))}, {e(source.get('published_date'))}</li>")
    explanations = "".join(f"<li>Reported event [{e(x['source_id'])}]: {e(x['reported_event'])}. "
                           f"Source excerpt: {e(x.get('evidence_quote'))}. "
                           f"Possible influence: {e(x['possible_influence'])}</li>" for x in summary.get("explanations", []))
    benchmark_text = ' '.join(item['text'] for item in summary.get('analysis', {}).get('observations', [])
                              if item['title'].startswith('Benchmark context'))
    return ("<!doctype html><html><body style='font:15px Arial,sans-serif;color:#18232f;max-width:760px;margin:auto'>"
            f"<h1>Daily Market Intelligence</h1><p>Trading date: <strong>{e(summary['date'])}</strong></p>"
            f"<h2>Market overview</h2><p>{e(summary['market_overview'])}</p>"
            f"<p>{e(benchmark_text)}</p>"
            f"<table style='border-collapse:collapse;width:100%'><tr><th>Stock</th><th>Close</th><th>Daily return</th><th>Signal</th></tr>{rows}</table>"
            f"<h2>Potential explanations and catalysts</h2><ul>{explanations or '<li>No verified news explanation available.</li>'}</ul>"
            f"<h2>Supporting reports</h2><ul>{''.join(links) or '<li>No eligible sources retrieved.</li>'}</ul>"
            f"<p>News status: {e(summary['news_status'])}. Provider/model: {e(summary['llm_source'])} / {e(summary['model'])}.</p>"
            f"<p>Last market fetch: {e(summary.get('last_successful_fetch'))}. Generated: {e(summary.get('generated_at'))}. "
            f"Analysis cutoff: {e(summary.get('analysis_cutoff'))}. Stale: {e(summary['stale'])}.</p>"
            "<p style='font-size:12px;color:#555'>For informational research only; not investment advice. "
            "Daily bars may be delayed or provisional. News associations do not establish causation.</p></body></html>")


def claim_delivery(engine, market_date, recipient):
    key = digest({"date": market_date, "recipient": recipient.strip().lower(), "kind": "daily-intelligence-v1"})
    table = AlertDelivery.__table__
    with exclusive(engine, "delivery:" + key) as connection:
        if connection is None:
            return {"allowed": False, "reason": "busy"}
        row = connection.execute(select(table).where(table.c.key == key)).mappings().first()
        if row and (row["status"] == "sent" or row["expires_at"] > time.time()):
            return {"allowed": False, "reason": "already_sent" if row["status"] == "sent" else "pending"}
        token = secrets.token_hex(24)
        connection.execute(table.delete().where(table.c.key == key))
        connection.execute(table.insert().values(key=key, token=token, status="pending", expires_at=time.time() + 600))
        connection.commit()
        return {"allowed": True, "key": key, "token": token}


def complete_delivery(engine, key, token):
    table = AlertDelivery.__table__
    with exclusive(engine, "delivery:" + key) as connection:
        if connection is None:
            return False
        row = connection.execute(select(table).where(table.c.key == key, table.c.token == token)).mappings().first()
        if not row:
            return False
        connection.execute(table.update().where(table.c.key == key, table.c.token == token).values(status="sent"))
        connection.commit()
        return True
