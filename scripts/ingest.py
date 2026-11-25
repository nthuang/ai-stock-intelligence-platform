"""Load the bundled offline snapshot or refresh live data once or periodically."""
import argparse
import logging
import os
from pathlib import Path
import time

import pandas as pd

from backend.db import engine
from backend.models import Base
from scripts.fetch_data import main as fetch_live, upsert_to_postgres

logger = logging.getLogger(__name__)
DEMO_PATH = Path(__file__).resolve().parent.parent / "data/processed/stocks_processed.csv"


def ingest(mode: str) -> None:
    Base.metadata.create_all(bind=engine)
    if mode == "demo":
        frame = pd.read_csv(DEMO_PATH)
        frame["Date"] = pd.to_datetime(frame["Date"])
        upsert_to_postgres(frame)
        logger.info("Loaded %d historical demo rows (latest %s)", len(frame), frame["Date"].max())
    else:
        fetch_live()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["demo", "live"], default=os.getenv("DATA_MODE", "live"))
    parser.add_argument("--interval-seconds", type=int, default=0)
    args = parser.parse_args()
    if args.interval_seconds < 0:
        parser.error("interval-seconds must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            ingest(args.mode)
        except Exception as exc:
            logger.error("Ingestion failed: %s", type(exc).__name__)
            if not args.interval_seconds:
                raise
        if not args.interval_seconds:
            break
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
