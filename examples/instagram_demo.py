import logging
import os
import sys

from dotenv import load_dotenv

from lobstr import Crawlers, Lobstr

logging.basicConfig(level=logging.INFO)


def main():
    load_dotenv()
    api_key = os.getenv("LOBSTR_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        print("Set LOBSTR_API_KEY (or API_KEY) in your .env file.")
        sys.exit(1)

    client = Lobstr(api_key=api_key)

    print("Starting Instagram scrape for @cristiano...")
    result = client.scrape(
        crawler_id=Crawlers.INSTAGRAM_PROFILE_SCRAPER,
        urls=["https://instagram.com/cristiano"],
    )

    print(f"\nScrape complete! {len(result.data)} row(s) returned.")
    if result.data:
        print("First row:", result.data[0])
    print(f"CSV saved to: {result.file_path}")


if __name__ == "__main__":
    main()
