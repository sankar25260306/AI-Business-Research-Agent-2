import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.browser_agent import BrowserAgent


async def main():
    engine = sys.argv[1] if len(sys.argv) > 1 else "duckduckgo"
    query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Dentists in Austin"
    browser = BrowserAgent()
    await browser.start()
    try:
        results = await browser.search_engine(engine, query, max_results=5)
        for result in results:
            print(f"{result['title']} | {result['url']}")
        print(f"RESULT_COUNT={len(results)}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
