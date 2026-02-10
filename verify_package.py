import sys
import os

# Add src to python path to simulate installed package
current_dir = os.getcwd()
src_dir = os.path.join(current_dir, "src")
sys.path.insert(0, src_dir)

try:
    print(f"Checking import from {src_dir}...")
    from lobstr import Lobstr, LobstrError, Crawlers, ScrapeResult, Squid, Run, RunStats
    print("Successfully imported: Lobstr, LobstrError, Crawlers, ScrapeResult, Squid, Run, RunStats")

    # Instantiate client
    client = Lobstr(api_key="test_key")
    print("Successfully instantiated Lobstr client")

    # Check Crawlers constant
    assert Crawlers.INSTAGRAM_PROFILE_SCRAPER == "5c55ffbf10fbbc7ee63ebb2bcaa332b7"
    print(f"Crawlers.INSTAGRAM_PROFILE_SCRAPER = {Crawlers.INSTAGRAM_PROFILE_SCRAPER}")

    # Check models
    squid = Squid(id="abc", crawler="xyz")
    print(f"Squid model works: id={squid.id}, crawler={squid.crawler}")

    run = Run(id="run1", squid="abc", status="running")
    print(f"Run model works: id={run.id}, status={run.status}")

    stats = RunStats(is_done=True, total_results=5)
    print(f"RunStats model works: is_done={stats.is_done}, total_results={stats.total_results}")

    result = ScrapeResult(data=[{"username": "test"}], file_path="test.csv", run=run)
    print(f"ScrapeResult model works: {len(result.data)} row(s), file={result.file_path}")

    print("\nAll checks passed!")

except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Unexpected error: {e}")
    sys.exit(1)
