"""Unit tests never read developer credentials or use the developer database."""
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DATA_MODE"] = "demo"
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", "sqlite://")
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("TAVILY_API_KEY", None)
os.environ["NEWS_ENABLED"] = "false"

import pytest


@pytest.fixture(autouse=True)
def block_external_ai(monkeypatch):
    """Tests must explicitly mock providers; developer keys never reach a network."""
    from openai._base_client import SyncAPIClient
    from tavily import TavilyClient

    def blocked(*args, **kwargs):
        raise AssertionError("Unmocked external AI request in automated test")
    monkeypatch.setattr(SyncAPIClient, "request", blocked)
    monkeypatch.setattr(TavilyClient, "search", blocked)
