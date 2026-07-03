"""
main.py
CLI entry point - runs the full pipeline (core/pipeline.py) without the
web UI, useful for scripting/testing.

Run:
    python main.py "Hair Salons in Lakeway, Texas"
"""

import asyncio
import json
import sys
from loguru import logger

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from core.pipeline import run_pipeline


async def _cli_emit(event_type, message, progress, data=None):
    logger.info(f"[{progress:>5.1f}%] {message}")


async def main():
    if len(sys.argv) < 2:
        print('Usage: python main.py "Hair Salons in Lakeway, Texas"')
        sys.exit(1)
    query = " ".join(sys.argv[1:])
    report = await run_pipeline(query, emit=_cli_emit)

    with open("data/last_run_output.json", "w") as f:
        json.dump(report, f, indent=2)

    logger.success(
        f"DONE. {report['businesses_fully_verified']} verified businesses. "
        f"Report written to data/last_run_output.json"
    )


if __name__ == "__main__":
    asyncio.run(main())