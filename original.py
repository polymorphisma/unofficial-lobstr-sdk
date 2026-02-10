#!/usr/bin/env python3
"""
Instagram Profile Scraper using Lobstr.io API
"""

from __future__ import annotations

import os
import sys
import time
import argparse
import logging
import signal
import csv
import tempfile
from typing import Optional, Dict, List, Any
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class Config:
    """Application configuration"""
    BASE_URL: str = "https://api.lobstr.io/v1"
    CRAWLER_ID: str = "5c55ffbf10fbbc7ee63ebb2bcaa332b7"

    # Polling intervals (seconds)
    UPLOAD_POLL_INTERVAL: int = 2
    RUN_POLL_INTERVAL: int = 3
    EXPORT_POLL_INTERVAL: int = 5
    ERROR_RETRY_INTERVAL: int = 5

    # Retry configuration
    MAX_RETRIES: int = 3
    BACKOFF_FACTOR: float = 0.5
    RETRY_STATUS_CODES: tuple = (500, 502, 503, 504, 429)

    # Request timeout (seconds)
    REQUEST_TIMEOUT: int = 30

    @classmethod
    def from_env(cls) -> Config:
        """Create config from environment variables"""
        return cls()


# ============================================================================
# Constants
# ============================================================================

SEPARATOR_LINE = "=" * 60


# ============================================================================
# Custom Exceptions
# ============================================================================

class ScraperException(Exception):
    """Base exception for scraper errors"""
    pass


class APIException(ScraperException):
    """API request failed"""
    pass


class UploadException(ScraperException):
    """File upload failed"""
    pass


class RunException(ScraperException):
    """Run execution failed"""
    pass


class ValidationException(ScraperException):
    """Input validation failed"""
    pass


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure application logging"""
    logger = logging.getLogger("instagram_scraper")
    logger.setLevel(level)

    # Only add handler if none exists (prevent duplication)
    if not logger.handlers:
        # Console handler with custom format
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        # Custom formatter for clean output
        formatter = logging.Formatter('%(message)s')
        handler.setFormatter(formatter)

        logger.addHandler(handler)

    return logger


# ============================================================================
# API Client
# ============================================================================

class InstagramProfileScraperAPI:
    """Lobstr.io Instagram Profile Scraper API Client"""

    def __init__(
        self,
        api_key: str,
        config: Config,
        logger: logging.Logger
    ):
        self.api_key = api_key
        self.config = config
        self.logger = logger
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry logic"""
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=self.config.MAX_RETRIES,
            backoff_factor=self.config.BACKOFF_FACTOR,
            status_forcelist=self.config.RETRY_STATUS_CODES,
            allowed_methods=["GET", "POST", "DELETE"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set default headers
        session.headers.update({
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json"
        })

        return session

    def _make_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> requests.Response:
        """Make HTTP request with error handling"""
        url = f"{self.config.BASE_URL}/{endpoint}"

        try:
            kwargs.setdefault('timeout', self.config.REQUEST_TIMEOUT)
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as e:
            error_msg = self._extract_error_message(e.response)
            raise APIException(f"API request failed: {error_msg}") from e
        except requests.exceptions.RequestException as e:
            raise APIException(f"Network error: {str(e)}") from e

    def _extract_error_message(self, response: requests.Response) -> str:
        """Extract error message from response"""
        try:
            error_data = response.json()
            return str(error_data)
        except (ValueError, requests.exceptions.JSONDecodeError):
            return response.text or f"HTTP {response.status_code}"

    def list_squids(self, crawler_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all squids, optionally filtered by crawler"""
        response = self._make_request("GET", "squids")
        squids = response.json().get("data", [])

        if crawler_id:
            squids = [s for s in squids if s.get("crawler") == crawler_id]

        return squids

    def create_squid(self, crawler_id: str) -> Dict[str, Any]:
        """Create a new squid"""
        response = self._make_request(
            "POST",
            "squids",
            json={"crawler": crawler_id}
        )
        return response.json()

    def update_squid(
        self,
        squid_hash: str,
        name: Optional[str] = None,
        concurrency: Optional[int] = None
    ) -> Dict[str, Any]:
        """Update squid settings"""
        data = {}
        if name is not None:
            data["name"] = name
        if concurrency is not None:
            data["concurrency"] = concurrency

        response = self._make_request(
            "POST",
            f"squids/{squid_hash}",
            json=data
        )
        return response.json()

    def empty_squid(self, squid_hash: str) -> Dict[str, Any]:
        """Empty all tasks from squid"""
        response = self._make_request(
            "POST",
            f"squids/{squid_hash}/empty",
            json={"type": "url"}
        )
        # Handle empty response (squid already empty)
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            # Empty response means squid was already empty or successfully emptied
            return {"status": "success", "message": "Squid emptied"}

    def delete_squid(self, squid_hash: str) -> bool:
        """Delete a squid"""
        self._make_request("DELETE", f"squids/{squid_hash}")
        return True

    def get_tasks(self, squid_hash: str, page: int = 1) -> Dict[str, Any]:
        """Get tasks for a squid"""
        response = self._make_request(
            "GET",
            f"tasks?squid={squid_hash}&type=url&page={page}"
        )
        return response.json()

    def has_tasks(self, squid_hash: str) -> bool:
        """Check if squid has any tasks"""
        try:
            result = self.get_tasks(squid_hash, page=1)
            data = result.get('data', [])
            return len(data) > 0
        except APIException as e:
            # If we can't check tasks (e.g., network error), log and assume no tasks
            self.logger.debug(f"Failed to check tasks: {e}")
            return False

    def add_tasks(self, squid_hash: str, urls: List[str]) -> Dict[str, Any]:
        """Add tasks to squid"""
        tasks = [{"url": url.strip()} for url in urls if url.strip()]
        response = self._make_request(
            "POST",
            "tasks",
            json={"tasks": tasks, "squid": squid_hash}
        )
        return response.json()

    def upload_tasks(self, squid_hash: str, file_path: Path) -> Dict[str, Any]:
        """
        Upload tasks from file.
        Handles .txt files by converting them to CSV with a 'url' header.
        """
        if not file_path.exists():
            raise ValidationException(f"File not found: {file_path}")

        # Use clean headers without Content-Type (let requests set multipart/form-data)
        headers = {"Authorization": f"Token {self.api_key}"}
        data = {'squid': squid_hash}
        file_ext = file_path.suffix.lower()

        try:
            if file_ext == '.txt':
                # Convert .txt to temporary .csv with header
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.csv', newline='') as temp_csv:
                    temp_csv_path = Path(temp_csv.name)
                    try:
                        writer = csv.writer(temp_csv)
                        writer.writerow(['url'])  # Add header
                        
                        count = 0
                        with file_path.open('r', encoding='utf-8') as src:
                            for line in src:
                                url = line.strip()
                                if url:
                                    writer.writerow([url])
                                    count += 1
                        
                        if count == 0:
                            raise ValidationException("Input file is empty")
                            
                        # Flush and seek to start for reading
                        temp_csv.flush()
                        temp_csv.seek(0)
                        
                        # Re-open for reading in binary mode for upload
                        with open(temp_csv_path, 'rb') as f_in:
                            files = {'file': (file_path.stem + '.csv', f_in, 'text/csv')}
                            response = requests.post(
                                f"{self.config.BASE_URL}/tasks/upload",
                                headers=headers,
                                files=files,
                                data=data,
                                timeout=self.config.REQUEST_TIMEOUT
                            )
                    finally:
                        # cleanup temp file
                        try:
                            temp_csv_path.unlink()
                        except OSError:
                            pass
            else:
                # Direct upload for other supported formats
                with file_path.open('rb') as f:
                    files = {'file': f}
                    response = requests.post(
                        f"{self.config.BASE_URL}/tasks/upload",
                        headers=headers,
                        files=files,
                        data=data,
                        timeout=self.config.REQUEST_TIMEOUT
                    )

            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_msg = self._extract_error_message(e.response)
            raise UploadException(f"Upload failed: {error_msg}") from e
        except Exception as e:
             if isinstance(e, ScraperException):
                 raise
             raise UploadException(f"Upload processing failed: {str(e)}") from e

    def get_upload_status(self, upload_task_id: str) -> Dict[str, Any]:
        """Check upload task status"""
        response = self._make_request("GET", f"tasks/upload/{upload_task_id}")
        return response.json()

    def start_run(self, squid_hash: str) -> Dict[str, Any]:
        """Start a run"""
        response = self._make_request(
            "POST",
            "runs",
            json={"squid": squid_hash}
        )
        return response.json()

    def get_run_stats(self, run_hash: str) -> Dict[str, Any]:
        """Get run statistics"""
        response = self._make_request("GET", f"runs/{run_hash}/stats")
        return response.json()

    def get_console_output(self, squid_hash: str, run_hash: str) -> str:
        """Get console output for a run"""
        response = self._make_request(
            "GET",
            f"console.json?squid={squid_hash}&run={run_hash}"
        )
        return response.text

    def get_squid_errors(
        self,
        squid_hash: str,
        run_hash: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get errors for a squid, optionally filtered by run"""
        response = self._make_request("GET", f"squids/{squid_hash}/errors")
        errors = response.json().get("data", [])

        if run_hash:
            errors = [e for e in errors if e.get("run") == run_hash]

        return errors

    def list_runs(self, squid_hash: str, page: int = 1) -> Dict[str, Any]:
        """List runs for a squid"""
        response = self._make_request("GET", f"runs?squid={squid_hash}&page={page}")
        return response.json()

    def download_run(self, run_hash: str) -> str:
        """Get download link for run results"""
        response = self._make_request("GET", f"runs/{run_hash}/download")
        return response.json().get("s3", "")

    def abort_run(self, run_hash: str) -> Dict[str, Any]:
        """Abort a running run"""
        response = self._make_request("POST", f"runs/{run_hash}/abort")
        return response.json()

    def close(self) -> None:
        """Close the session and cleanup resources"""
        if self.session:
            self.session.close()

    def __enter__(self) -> InstagramProfileScraperAPI:
        """Context manager entry"""
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Context manager exit"""
        self.close()


# ============================================================================
# Upload Status Tracker
# ============================================================================

class UploadStatus(Enum):
    """Upload task states"""
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class UploadTracker:
    """Tracks file upload progress"""

    def __init__(self, api: InstagramProfileScraperAPI, logger: logging.Logger):
        self.api = api
        self.logger = logger

    def wait_for_completion(self, upload_task_id: str) -> Dict[str, Any]:
        """Poll upload status until completion"""
        self.logger.info("Checking upload status...")

        while True:
            try:
                status = self.api.get_upload_status(upload_task_id)
                state = status.get('state', 'UNKNOWN')

                if state == UploadStatus.SUCCESS.value:
                    meta = status.get('meta', {})
                    self.logger.info("Upload completed successfully!")
                    self.logger.info(f"  Valid: {meta.get('valid', 0)}")
                    self.logger.info(f"  Inserted: {meta.get('inserted', 0)}")
                    self.logger.info(f"  Duplicates: {meta.get('duplicates', 0)}")
                    self.logger.info(f"  Invalid: {meta.get('invalid', 0)}")
                    return meta
                elif state == UploadStatus.FAILURE.value:
                    raise UploadException("Upload failed")
                else:
                    self.logger.info(f"  Status: {state}...")
                    time.sleep(self.api.config.UPLOAD_POLL_INTERVAL)

            except ScraperException:
                raise
            except Exception as e:
                self.logger.warning(f"Error checking upload status: {e}")
                time.sleep(self.api.config.ERROR_RETRY_INTERVAL)


# ============================================================================
# Run Monitor
# ============================================================================

class RunMonitor:
    """Monitors and displays run progress"""

    def __init__(
        self,
        api: InstagramProfileScraperAPI,
        logger: logging.Logger
    ):
        self.api = api
        self.logger = logger
        self.last_console_output = ""
        self.seen_error_ids = set()

    def _fetch_and_display_console(self, squid_hash: str, run_hash: str) -> None:
        """Fetch and display new console output"""
        try:
            console_output = self.api.get_console_output(squid_hash, run_hash)

            if console_output != self.last_console_output:
                if self.last_console_output:
                    # Only print new lines
                    new_lines = console_output[len(self.last_console_output):]
                    print(new_lines, end='')
                else:
                    # First time, print everything
                    print(console_output)

                self.last_console_output = console_output
        except Exception as e:
            self.logger.debug(f"Failed to fetch console output: {e}")

    def _check_for_errors(self, squid_hash: str, run_hash: str) -> bool:
        """Check for new errors, returns True if errors found"""
        try:
            errors = self.api.get_squid_errors(squid_hash, run_hash=run_hash)
            new_errors = [e for e in errors if e.get('id') not in self.seen_error_ids]

            if new_errors:
                for error in new_errors:
                    self.seen_error_ids.add(error.get('id'))
                    self._display_error(error)

                return True
        except Exception as e:
            self.logger.debug(f"Failed to check errors: {e}")

        return False

    def _display_error(self, error: Dict[str, Any]) -> None:
        """Display error in formatted way"""
        self.logger.error("\n" + SEPARATOR_LINE)
        self.logger.error("ERROR DETECTED - STOPPING RUN")
        self.logger.error(SEPARATOR_LINE)
        self.logger.error(f"Title: {error.get('title', 'N/A')}")
        self.logger.error(f"Description: {error.get('description', 'N/A')}")
        self.logger.error(f"Time: {error.get('created_at', 'N/A')}")
        self.logger.error(SEPARATOR_LINE + "\n")

    def monitor_until_complete(
        self,
        squid_hash: str,
        run_hash: str
    ) -> Dict[str, Any]:
        """Monitor run until completion or error"""
        self.logger.info("(Press Ctrl+C to abort)\n")

        while True:
            try:
                # Check for errors first
                if self._check_for_errors(squid_hash, run_hash):
                    raise RunException("Run failed with errors")

                # Get run stats
                stats = self.api.get_run_stats(run_hash)
                is_done = stats.get('is_done', False)

                # Display console output
                self._fetch_and_display_console(squid_hash, run_hash)

                if is_done:
                    # Fetch final console output
                    self._fetch_and_display_console(squid_hash, run_hash)

                    self.logger.info("\n" + SEPARATOR_LINE)
                    self.logger.info("Run completed!")
                    self.logger.info(f"  Duration: {stats.get('duration', 'N/A')}")
                    self.logger.info(f"  Total results: {stats.get('total_results', 'N/A')}")
                    self.logger.info(SEPARATOR_LINE)
                    return stats

                time.sleep(self.api.config.RUN_POLL_INTERVAL)

            except (ScraperException, KeyboardInterrupt):
                raise
            except Exception as e:
                self.logger.error(f"Error polling run: {e}")
                time.sleep(self.api.config.ERROR_RETRY_INTERVAL)


# ============================================================================
# Export Manager
# ============================================================================

class ExportManager:
    """Manages result export and download"""

    def __init__(self, api: InstagramProfileScraperAPI, logger: logging.Logger):
        self.api = api
        self.logger = logger

    def wait_for_export(self, squid_hash: str, run_hash: str) -> bool:
        """Wait for export to be ready"""
        self.logger.info("Checking export status...")

        while True:
            try:
                runs = self.api.list_runs(squid_hash, page=1)
                run_data = next(
                    (r for r in runs.get('data', []) if r.get('id') == run_hash),
                    None
                )

                if not run_data:
                    raise RunException("Run not found")

                if run_data.get('export_done', False):
                    self.logger.info("Export ready!")
                    return True

                self.logger.info("  Waiting for export to complete...")
                time.sleep(self.api.config.EXPORT_POLL_INTERVAL)

            except ScraperException:
                raise
            except Exception as e:
                self.logger.error(f"Error checking export status: {e}")
                time.sleep(self.api.config.ERROR_RETRY_INTERVAL)

    def get_download_url(self, run_hash: str) -> str:
        """Get S3 download URL"""
        self.logger.info("Getting download link...")
        url = self.api.download_run(run_hash)

        if not url:
            raise RunException("No download link available")

        return url

    def download_file(self, url: str, run_hash: str, output_dir: Path = None) -> Path:
        """Download CSV file from S3 using streaming"""
        if output_dir is None:
            output_dir = Path.cwd()

        filename = output_dir / f"instagram_profiles_{run_hash}.csv"

        self.logger.info("Downloading...")
        # Use requests.get directly - S3 pre-signed URLs are sensitive to extra headers
        # Stream=True to avoid loading entire file into memory
        with requests.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        self.logger.info(f"Downloaded to: {filename}")
        return filename


# ============================================================================
# User Interface Layer
# ============================================================================

class UserInterface:
    """Handles user interaction and display"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def print_header(self, title: str) -> None:
        """Print section header"""
        self.logger.info("\n" + SEPARATOR_LINE)
        self.logger.info(title)
        self.logger.info(SEPARATOR_LINE)

    def confirm(self, message: str) -> bool:
        """Get yes/no confirmation from user"""
        response = input(f"{message} (y/n): ").strip().lower()
        return response == 'y'

    def get_input(self, prompt: str, required: bool = False) -> str:
        """Get text input from user"""
        while True:
            value = input(f"{prompt}: ").strip()
            if value or not required:
                return value
            self.logger.error("This field cannot be empty.")

    def choose_squid(
        self,
        squids: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Interactive squid selection"""
        self.print_header("SQUID SELECTION")

        if not squids:
            self.logger.info("\nNo Instagram Profile Scraper squids found.")
            if self.confirm("Create a new squid?"):
                return None
            else:
                raise ValidationException("Cannot proceed without a squid")

        self.logger.info(f"\nFound {len(squids)} squid(s):\n")

        for idx, squid in enumerate(squids, 1):
            self.logger.info(f"  [{idx}] {squid.get('name', 'Unnamed')}")
            self.logger.info(f"      ID: {squid.get('id', 'N/A')}")
            self.logger.info(f"      Concurrency: {squid.get('concurrency', 'N/A')}")
            self.logger.info(f"      Last Run: {squid.get('last_run_at', 'Never')}")
            self.logger.info(f"      Last Run Done: {squid.get('last_run_is_done', 'N/A')}")
            self.logger.info("")

        self.logger.info(f"  [0] Create new squid\n")

        while True:
            try:
                choice = input(f"Choose a squid [0-{len(squids)}]: ").strip()
                choice_num = int(choice)

                if choice_num == 0:
                    return None
                elif 1 <= choice_num <= len(squids):
                    return squids[choice_num - 1]
                else:
                    self.logger.error(
                        f"Invalid choice. Enter a number between 0 and {len(squids)}"
                    )
            except ValueError:
                self.logger.error("Invalid input. Please enter a number.")

    def get_squid_name(self) -> str:
        """Get squid name from user"""
        return self.get_input("\nEnter squid name", required=True)


# ============================================================================
# Main Application Controller
# ============================================================================

class InstagramScraperApp:
    """Main application controller"""

    def __init__(self, config: Config, api_key: str):
        self.config = config
        self.logger = setup_logging()
        self.api = InstagramProfileScraperAPI(api_key, config, self.logger)
        self.ui = UserInterface(self.logger)
        self.current_run_hash: Optional[str] = None

        # Setup signal handler
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, _: int, __: Any) -> None:
        """Handle keyboard interrupt"""
        self.logger.warning("\n\nInterrupt detected!")

        if self.current_run_hash:
            if self.ui.confirm("Do you want to abort the current run?"):
                try:
                    self.api.abort_run(self.current_run_hash)
                    self.logger.info(f"Run {self.current_run_hash} aborted")
                except Exception as e:
                    self.logger.error(f"Error aborting run: {e}")

        self.logger.info("Exiting...")
        sys.exit(0)

    def select_or_create_squid(self) -> tuple[str, bool]:
        """Select existing squid or create new one

        Returns:
            tuple[str, bool]: (squid_hash, is_new)
        """
        squids = self.api.list_squids(crawler_id=self.config.CRAWLER_ID)
        selected = self.ui.choose_squid(squids)

        if selected:
            return selected.get('id'), False
        else:
            return self._create_new_squid(), True

    def _create_new_squid(self) -> str:
        """Create and configure new squid"""
        self.ui.print_header("CREATE NEW SQUID")

        self.logger.info("\nCreating squid...")
        squid = self.api.create_squid(self.config.CRAWLER_ID)
        squid_hash = squid.get('id')
        self.logger.info(f"Squid created: {squid_hash}")

        # Get name
        name = self.ui.get_squid_name()
        self.api.update_squid(squid_hash, name=name)
        self.logger.info(f"Squid named: {name}")

        return squid_hash

    def configure_squid(
        self,
        squid_hash: str,
        concurrency: int,
        empty: bool = False
    ) -> None:
        """Configure squid settings"""
        # Only prompt to empty if squid has tasks
        if empty:
            if self.api.has_tasks(squid_hash):
                if self.ui.confirm("\nEmpty squid (delete all existing tasks)?"):
                    self.logger.info("Emptying squid...")
                    self.api.empty_squid(squid_hash)
                    self.logger.info("Squid emptied")
            # If no tasks, silently skip

        # Update concurrency
        self.api.update_squid(squid_hash, concurrency=concurrency)
        self.logger.info(f"Concurrency set to {concurrency}")

    def add_tasks(
        self,
        squid_hash: str,
        url: Optional[str] = None,
        file_path: Optional[Path] = None
    ) -> None:
        """Add tasks to squid"""
        self.ui.print_header("ADDING TASKS")

        if file_path:
            self.logger.info(f"\nUploading tasks from file: {file_path}")
            upload_result = self.api.upload_tasks(squid_hash, file_path)
            upload_task_id = upload_result.get('id')
            self.logger.info(f"Upload initiated: {upload_task_id}\n")

            # Wait for upload to complete
            tracker = UploadTracker(self.api, self.logger)
            tracker.wait_for_completion(upload_task_id)

        elif url:
            self.logger.info(f"\nAdding single URL: {url}")
            self.api.add_tasks(squid_hash, [url])
            self.logger.info("Task added successfully")

        else:
            raise ValidationException("No URL or file provided")

    def run_scraper(self, squid_hash: str) -> str:
        """Execute scraping run"""
        self.ui.print_header("STARTING RUN")

        run = self.api.start_run(squid_hash)
        run_hash = run.get('id')
        self.current_run_hash = run_hash

        try:
            monitor = RunMonitor(self.api, self.logger)
            monitor.monitor_until_complete(squid_hash, run_hash)
            return run_hash
        finally:
            self.current_run_hash = None

    def download_results(self, squid_hash: str, run_hash: str) -> None:
        """Download scraping results"""
        self.ui.print_header("DOWNLOADING RESULTS")

        manager = ExportManager(self.api, self.logger)

        # Wait for export
        manager.wait_for_export(squid_hash, run_hash)

        # Get download URL
        download_url = manager.get_download_url(run_hash)
        self.logger.info(f"\nDownload link:\n  {download_url}\n")

        # Download file
        try:
            output_file = manager.download_file(download_url, run_hash)
            self.logger.info(f"File saved: {output_file}")
        except Exception as e:
            self.logger.error(f"Error downloading file: {e}")
            self.logger.info(f"Manual download: {download_url}")

    def run(
        self,
        url: Optional[str] = None,
        file_path: Optional[Path] = None,
        concurrency: int = 1
    ) -> None:
        """Main application flow"""
        self.ui.print_header("INSTAGRAM PROFILE SCRAPER")

        # Select or create squid
        squid_hash, is_new = self.select_or_create_squid()
        self.logger.info(f"\nUsing squid: {squid_hash}")

        # Configure squid (only prompt to empty if it's an existing squid)
        self.configure_squid(squid_hash, concurrency, empty=not is_new)

        # Add tasks
        self.add_tasks(squid_hash, url=url, file_path=file_path)

        # Run scraper
        run_hash = self.run_scraper(squid_hash)

        # Download results
        self.download_results(squid_hash, run_hash)

        self.ui.print_header("COMPLETED")


# ============================================================================
# CLI Entry Point
# ============================================================================

def validate_args(args: argparse.Namespace) -> None:
    """Validate command line arguments"""
    if not args.url and not args.file:
        raise ValidationException("Either --url or --file must be provided")

    if args.url and args.file:
        raise ValidationException("Cannot use both --url and --file")

    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            raise ValidationException(f"File not found: {file_path}")

        if file_path.suffix.lower() not in ['.txt', '.csv', '.tsv']:
            raise ValidationException(
                f"Unsupported file type: {file_path.suffix}. "
                "Use .txt, .csv, or .tsv"
            )

    if args.slots < 1:
        raise ValidationException("Concurrency slots must be at least 1")


def main() -> None:
    """Main CLI function"""
    parser = argparse.ArgumentParser(
        description="Instagram Profile Scraper using Lobstr.io API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --url https://instagram.com/user123
  %(prog)s --file urls.txt --slots 5
        """
    )

    parser.add_argument(
        "--url",
        type=str,
        help="Single Instagram URL to scrape"
    )
    parser.add_argument(
        "--file",
        type=str,
        help="File containing URLs (one per line, .txt/.csv/.tsv)"
    )
    parser.add_argument(
        "--slots", "-s",
        type=int,
        default=1,
        help="Concurrency slots (default: 1)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    try:
        # Validate arguments
        validate_args(args)

        # Load configuration
        config = Config.from_env()

        # Get API key
        load_dotenv()
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise ValidationException("API_KEY not found in .env file")

        # Create and run application
        app = InstagramScraperApp(config, api_key)

        # Set log level
        if args.verbose:
            app.logger.setLevel(logging.DEBUG)

        # Run with provided arguments
        file_path = Path(args.file) if args.file else None
        app.run(url=args.url, file_path=file_path, concurrency=args.slots)

    except ValidationException as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(1)
    except ScraperException as e:
        print(f"Scraper error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        if 'args' in locals() and hasattr(args, 'verbose') and args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
