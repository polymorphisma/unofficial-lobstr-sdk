import os
import requests
import argparse
import time
import logging
import csv
import sys
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive",
}


# Global session for connection pooling
session = requests.Session()
session.headers.update(DEFAULT_HEADERS)

def get_csv_download_url(run_id, api_key):
    """
    Fetches the S3 URL for the CSV export of a run.
    """
    url = f"https://api.lobstr.io/v1/runs/{run_id}/download"
    headers = {'Authorization': f'Token {api_key}'}
    try:
        response = session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get('s3')
    except requests.RequestException as e:
        logger.error(f"Failed to get CSV URL: {e}")
        return None

def download_file_with_progress(url, filepath):
    """
    Downloads a file with a progress bar, retries, and resume capability.
    """
    filename = os.path.basename(filepath)
    
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            resume_header = {}
            mode = 'wb'
            existing_size = 0
            
            if os.path.exists(filepath):
                existing_size = os.path.getsize(filepath)
                if existing_size > 0:
                    resume_header = {'Range': f'bytes={existing_size}-'}
                    mode = 'ab' # Append mode

            # If it's a retry and we have some data, we try to resume
            # If it's first attempt and we have data, we also try to resume (or check if done)
            
            with session.get(url, headers=resume_header, stream=True, timeout=30) as r:
                # Handle already completed file (416 Range Not Satisfiable typically means range is out of bounds properly)
                if r.status_code == 416: 
                    logger.info(f"[skip] {filename} already fully downloaded (416)")
                    return True

                if r.status_code >= 400:
                    logger.warning(f"[fail] HTTP {r.status_code} for {filename} (Attempt {attempt}/{max_retries})")
                    if attempt < max_retries and r.status_code >= 500:
                         time.sleep(1.5 ** attempt)
                         continue
                    return False
                
                total_length = r.headers.get('content-length')
                is_resumed = (r.status_code == 206)
                
                if is_resumed:
                    if total_length:
                        total_length = int(total_length) + existing_size
                    logger.info(f"Resuming {filename} from {existing_size/1024/1024:.2f} MB...")
                else:
                    if existing_size > 0:
                        logger.info(f"Server did not accept resume. Restarting {filename}...")
                        mode = 'wb' # Reset to write mode
                        existing_size = 0
                    else:
                        logger.info(f"Downloading {filename}...")
                    
                    if total_length:
                        total_length = int(total_length)
                
                dl = existing_size
                
                # Initial progress print
                if total_length:
                    done = int(50 * dl / total_length)
                    percent = (dl / total_length) * 100
                    sys.stdout.write(f"\r[{'=' * done}{' ' * (50-done)}] {percent:.1f}% ({dl/1024/1024:.2f} MB / {total_length/1024/1024:.2f} MB)")
                else:
                    sys.stdout.write(f"\rDownloaded {dl/1024/1024:.2f} MB")
                sys.stdout.flush()

                with open(filepath, mode) as f:
                    # Increased chunk size to 1MB for better throughput
                    for data in r.iter_content(chunk_size=1024 * 1024):
                        if data:
                            dl += len(data)
                            f.write(data)
                            
                            if total_length:
                                done = int(50 * dl / total_length)
                                percent = (dl / total_length) * 100
                                sys.stdout.write(f"\r[{'=' * done}{' ' * (50-done)}] {percent:.1f}% ({dl/1024/1024:.2f} MB / {total_length/1024/1024:.2f} MB)")
                            else:
                                sys.stdout.write(f"\rDownloaded {dl/1024/1024:.2f} MB")
                            sys.stdout.flush()
                print() # Newline after progress bar
            
            # Simple validation: if we got here without exception, assume success. 
            # Ideally verify content-length if known.
            return True

        except requests.RequestException as e:
            logger.warning(f"[err ] Error downloading {filename}: {e} (Attempt {attempt}/{max_retries})")
            if attempt < max_retries:
                time.sleep(1.5 ** attempt)
            else:
                logger.error(f"Failed to download {filename} after {max_retries} attempts.")
                return False
        except Exception as e:
            logger.error(f"Unexpected error for {filename}: {e}")
            return False

def fetch_and_download(run_id):
    api_key = os.getenv('LOBSTR_API_KEY')
    if not api_key:
        logger.error("Error: LOBSTR_API_KEY not found in .env file.")
        return

    # Prepare output directory
    dir_name = f"downloads_{run_id}"
    os.makedirs(dir_name, exist_ok=True)
    logger.info(f"Output directory: {dir_name}")

    # 1. Get CSV URL
    csv_url = get_csv_download_url(run_id, api_key)
    if not csv_url:
        logger.error("Could not retrieve CSV download URL.")
        return

    # 2. Download CSV
    csv_path = os.path.join(dir_name, "results.csv")
    # Using the same downloader for CSV (resumable!)
    if not download_file_with_progress(csv_url, csv_path):
        logger.error("Failed to download CSV results.")
        return

    # 3. Parse CSV and Download Videos
    logger.info("Parsing CSV and downloading videos...")
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            # Identify columns
            video_col = "video_url"
            id_col = "reel_id" # Default id col
            
            if reader.fieldnames:
                # Find Video URL column
                for col in reader.fieldnames:
                    clean_col = col.strip()
                    if clean_col == "VIDEO URL":
                        video_col = col
                    elif clean_col.lower() == "video_url":
                        video_col = col
                    
                    # Find Reel ID column
                    if clean_col == "REEL ID":
                        id_col = col
                    elif clean_col.lower() == "reel_id":
                        id_col = col
            
            # logger.info(f"Using column '{video_col}' for video URLs and '{id_col}' for filenames.")

            count = 0
            for row in reader:
                video_url = row.get(video_col)
                # Try getting reel_id, fallback to id, then ID
                result_id = row.get(id_col) or row.get('id') or row.get('ID')
                
                if video_url:
                    filename = f"{result_id}.mp4" if result_id else f"video_{count}.mp4"
                    filepath = os.path.join(dir_name, filename)
                    download_file_with_progress(video_url, filepath)
                    count += 1
                
    except Exception as e:
        logger.error(f"Error parsing CSV: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Lobstr results in batches and download videos.")
    parser.add_argument('--run', required=True, help='Lobstr Run ID')
    
    args = parser.parse_args()
    
    try:
        fetch_and_download(args.run)
    except KeyboardInterrupt:
        print("\n[!] Loop interrupted by user. Exiting gracefully.")
        sys.exit(0)