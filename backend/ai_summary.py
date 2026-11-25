import os
import logging
import json
from backend.analysis import analyze_snapshot, analysis_text

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
logger = logging.getLogger(__name__)


def _get_client() -> OpenAI:
    if os.getenv("DATA_MODE", "live") != "live":
        raise RuntimeError("External generation is disabled in offline mode")
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

    analysis = analyze_snapshot(signals)
    text_data = json.dumps(analysis, default=str)

    prompt = f"""
    You are a financial analyst.

    Choose which of the supplied computed interpretations matter most for this
    session. Return JSON with a focus list containing one to three exact observation
    titles from the data, ordered by importance. Do not write new prose or calculate
    numbers. The application will display the corresponding verified interpretations.

    {text_data}

    Do not invent titles or add facts, causes, forecasts or trading recommendations.
    """

    try:
        response = _get_client().chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=20,
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Empty provider response")
        focus = json.loads(content).get('focus')
        known = {item['title'] for item in analysis['observations']}
        if not isinstance(focus, list) or not 1 <= len(focus) <= 3 or any(not isinstance(title, str) or title not in known for title in focus):
            raise ValueError('Unknown interpretation')
        if len(set(focus)) != len(focus):
            raise ValueError('Duplicate interpretation')
        return {
            "summary": analysis_text(analysis),
            "focus": focus,
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
        if os.getenv("DATA_MODE", "live") != "live":
            reason = "External AI is disabled in demo mode."
        return {
            "summary": analysis_text(analysis),
            "provider_error": reason,
            "source": "fallback",
            "model": "none",
        }
