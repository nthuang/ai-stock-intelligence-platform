"""Unit tests never read developer credentials or use the developer database."""
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DATA_MODE"] = "demo"
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", "sqlite://")
os.environ.pop("GROQ_API_KEY", None)
