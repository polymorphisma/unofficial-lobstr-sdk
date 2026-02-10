from __future__ import annotations

import csv
import logging
import time
from typing import Any, Dict, List, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Crawlers, Run, RunStats, ScrapeResult, Squid

logger = logging.getLogger("lobstr")


class LobstrError(Exception):
    """Base exception for Lobstr SDK errors."""


class Lobstr:
    """Client for the Lobstr.io API.

    Provides both low-level methods (1:1 with REST endpoints) and a high-level
    ``scrape()`` helper that handles the full squid -> tasks -> run -> poll ->
    download flow automatically.
    """

    BASE_URL = "https://api.lobstr.io/v1"

    # Polling intervals (seconds)
    RUN_POLL_INTERVAL = 3
    EXPORT_POLL_INTERVAL = 5

    # Retry config
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 0.5
    RETRY_STATUS_CODES = (500, 502, 503, 504, 429)

    REQUEST_TIMEOUT = 30

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = self._build_session()

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def _build_session(self) -> requests.Session:
        session = requests.Session()

        retry = Retry(
            total=self.MAX_RETRIES,
            backoff_factor=self.BACKOFF_FACTOR,
            status_forcelist=self.RETRY_STATUS_CODES,
            allowed_methods=["GET", "POST", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update({
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "application/json",
        })
        return session

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.BASE_URL}/{path}"
        kwargs.setdefault("timeout", self.REQUEST_TIMEOUT)
        resp = self.session.request(method, url, **kwargs)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise LobstrError(f"{method} {path} -> {resp.status_code}: {detail}") from exc
        return resp

    # ------------------------------------------------------------------
    # Squids
    # ------------------------------------------------------------------

    def list_squids(self, crawler_id: Optional[str] = None) -> List[Squid]:
        """GET /v1/squids — list all squids, optionally filtered by crawler."""
        resp = self._request("GET", "squids")
        squids = [Squid.from_dict(s) for s in resp.json().get("data", [])]
        if crawler_id:
            squids = [s for s in squids if s.crawler == crawler_id]
        return squids

    def create_squid(self, crawler_id: str) -> Squid:
        """POST /v1/squids — create a new squid for *crawler_id*."""
        resp = self._request("POST", "squids", json={"crawler": crawler_id})
        return Squid.from_dict(resp.json())

    def update_squid(
        self,
        squid_id: str,
        *,
        name: Optional[str] = None,
        concurrency: Optional[int] = None,
    ) -> Squid:
        """POST /v1/squids/{id} — update squid settings."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if concurrency is not None:
            body["concurrency"] = concurrency
        resp = self._request("POST", f"squids/{squid_id}", json=body)
        return Squid.from_dict(resp.json())

    def delete_squid(self, squid_id: str) -> None:
        """DELETE /v1/squids/{id}"""
        self._request("DELETE", f"squids/{squid_id}")

    def empty_squid(self, squid_id: str) -> None:
        """POST /v1/squids/{id}/empty — remove all tasks from a squid."""
        self._request("POST", f"squids/{squid_id}/empty", json={"type": "url"})

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def add_tasks(self, squid_id: str, urls: List[str]) -> Dict[str, Any]:
        """POST /v1/tasks — add URL tasks to a squid."""
        tasks = [{"url": u.strip()} for u in urls if u.strip()]
        resp = self._request("POST", "tasks", json={"tasks": tasks, "squid": squid_id})
        return resp.json()

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def start_run(self, squid_id: str) -> Run:
        """POST /v1/runs — kick off a new run."""
        resp = self._request("POST", "runs", json={"squid": squid_id})
        return Run.from_dict(resp.json())

    def get_run_stats(self, run_id: str) -> RunStats:
        """GET /v1/runs/{id}/stats"""
        resp = self._request("GET", f"runs/{run_id}/stats")
        return RunStats.from_dict(resp.json())

    def abort_run(self, run_id: str) -> Dict[str, Any]:
        """POST /v1/runs/{id}/abort"""
        return self._request("POST", f"runs/{run_id}/abort").json()

    def list_runs(self, squid_id: str) -> List[Run]:
        """GET /v1/runs?squid={id}"""
        resp = self._request("GET", f"runs?squid={squid_id}")
        return [Run.from_dict(r) for r in resp.json().get("data", [])]

    def download_run(self, run_id: str) -> str:
        """GET /v1/runs/{id}/download — returns the S3 pre-signed URL."""
        resp = self._request("GET", f"runs/{run_id}/download")
        return resp.json().get("s3", "")

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------

    def _poll_until_done(self, run_id: str) -> RunStats:
        """Poll ``get_run_stats`` until ``is_done`` is True."""
        logger.info("Waiting for run %s to finish...", run_id)
        while True:
            stats = self.get_run_stats(run_id)
            if stats.is_done:
                logger.info(
                    "Run %s done — duration=%s, results=%s",
                    run_id,
                    stats.duration,
                    stats.total_results,
                )
                return stats
            time.sleep(self.RUN_POLL_INTERVAL)

    def _wait_for_export(self, squid_id: str, run_id: str) -> None:
        """Poll ``list_runs`` until ``export_done`` is True for *run_id*."""
        logger.info("Waiting for export...")
        while True:
            runs = self.list_runs(squid_id)
            run_data = next((r for r in runs if r.id == run_id), None)
            if run_data and run_data.export_done:
                logger.info("Export ready.")
                return
            time.sleep(self.EXPORT_POLL_INTERVAL)

    def _download_csv(self, run_id: str) -> tuple[List[Dict[str, Any]], str]:
        """Download the CSV for *run_id*, parse it, and return (rows, filepath)."""
        s3_url = self.download_run(run_id)
        if not s3_url:
            raise LobstrError("No download URL returned for run %s" % run_id)

        # Stream download — S3 pre-signed URLs must not carry our auth headers
        resp = requests.get(s3_url, stream=True, timeout=300)
        resp.raise_for_status()

        filename = f"lobstr_results_{run_id}.csv"
        with open(filename, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                fh.write(chunk)

        logger.info("Downloaded results to %s", filename)

        # Parse CSV into list of dicts
        rows: List[Dict[str, Any]] = []
        with open(filename, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(dict(row))

        return rows, filename

    def scrape(
        self,
        crawler_id: str,
        urls: List[str],
        *,
        sync: bool = True,
        concurrency: int = 1,
    ) -> Union[ScrapeResult, Run]:
        """High-level scrape: reuse or create squid, add tasks, run, poll, download.

        If a squid already exists for *crawler_id* it will be reused (emptied
        first) so that free-plan slot limits are respected.

        Parameters
        ----------
        crawler_id:
            The Lobstr crawler hash (see ``Crawlers`` constants).
        urls:
            URLs to scrape.
        sync:
            If ``True`` (default), block until the run finishes, download the
            CSV, and return a ``ScrapeResult`` with parsed data + file path.
            If ``False``, return the ``Run`` immediately after starting it.
        concurrency:
            Number of concurrent slots for the squid.
        """
        # 1. Reuse existing squid or create a new one
        existing = self.list_squids(crawler_id=crawler_id)
        if existing:
            squid = existing[0]
            logger.info("Reusing existing squid %s", squid.id)
            self.empty_squid(squid.id)
        else:
            squid = self.create_squid(crawler_id)
            logger.info("Created squid %s", squid.id)

        # 2. Set concurrency
        if concurrency != squid.concurrency:
            self.update_squid(squid.id, concurrency=concurrency)

        # 3. Add tasks
        self.add_tasks(squid.id, urls)
        logger.info("Added %d task(s)", len(urls))

        # 4. Start run
        run = self.start_run(squid.id)
        logger.info("Started run %s", run.id)

        if not sync:
            return run

        # 5. Poll until done
        self._poll_until_done(run.id)

        # 6. Wait for export
        self._wait_for_export(squid.id, run.id)

        # 7. Download & parse CSV
        data, file_path = self._download_csv(run.id)

        # Refresh run info
        runs = self.list_runs(squid.id)
        final_run = next((r for r in runs if r.id == run.id), run)

        return ScrapeResult(data=data, file_path=file_path, run=final_run)
