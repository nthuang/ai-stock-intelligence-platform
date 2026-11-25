import os
import logging

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
logger = logging.getLogger(__name__)


def _get_client() -> OpenAI:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set")

    return OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
        max_retries=1,
    )


def generate_summary(signals: list[dict]) -> dict:
    if not signals:
        return {
            "summary": "No significant market movements in the latest stored snapshot.",
            "source": "rule",
            "model": "none",
        }

    text_data = "\n".join(
        [
            f"{s.get('Date', 'Latest session')} {s['Ticker']}: {s['Signal']} ({s['Daily Return %']}%)"
            for s in signals
        ]
    )

    prompt = f"""
    You are a financial analyst.

    Based on the following stock signals, write a short 2-3 sentence summary of market activity:

    {text_data}

    Describe only supplied observations. Do not invent causes, news, forecasts,
    or trading recommendations. These are historical alerts, not predictions.
    Be concise and professional.
    """

    try:
        response = _get_client().chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=20,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Empty provider response")
        return {
            "summary": content.strip(),
            "source": "groq",
            "model": GROQ_MODEL,
        }
    except Exception as exc:
        logger.warning("Summary provider unavailable: %s", type(exc).__name__)
        error_type = type(exc).__name__
        reasons = {
            "AuthenticationError": "Groq rejected the API key. Check GROQ_API_KEY.",
            "PermissionDeniedError": "Groq denied access to this model or account.",
            "NotFoundError": "Configured Groq model was not found. Check GROQ_MODEL.",
            "RateLimitError": "Groq rate limit or quota reached. Retry later.",
            "APITimeoutError": "Groq request timed out. Retry shortly.",
            "APIConnectionError": "Cannot connect to Groq. Check network access.",
            "RuntimeError": "GROQ_API_KEY is not configured.",
        }
        reason = reasons.get(error_type, "Summary provider is temporarily unavailable.")
        moves = [f"{s['Ticker']} {s['Daily Return %']:+.2f}%" for s in signals
                 if isinstance(s.get('Daily Return %'), (int, float))]
        return {
            "summary": "Rule-based signals: " + (", ".join(moves) if moves else "No valid daily returns available."),
            "provider_error": reason,
            "source": "fallback",
            "model": "none",
        }
