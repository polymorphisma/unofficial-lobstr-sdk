"""Tests for the lobstr SDK — all API calls are mocked."""

from __future__ import annotations

import csv
import os
import textwrap
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from lobstr import Crawlers, Lobstr, LobstrError, Run, RunStats, ScrapeResult, Squid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_KEY = "test-api-key-123"


@pytest.fixture()
def client():
    return Lobstr(api_key=FAKE_KEY)


def _json_response(data, status_code=200):
    """Build a fake requests.Response that returns *data* from .json()."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    resp.text = str(data)
    return resp


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestSquidModel:
    def test_from_dict_minimal(self):
        s = Squid.from_dict({"id": "abc", "crawler": "xyz"})
        assert s.id == "abc"
        assert s.crawler == "xyz"
        assert s.name == ""
        assert s.concurrency == 1

    def test_from_dict_full(self):
        s = Squid.from_dict({
            "id": "abc",
            "crawler": "xyz",
            "name": "My Squid",
            "concurrency": 5,
            "last_run_at": "2025-01-01",
            "last_run_is_done": True,
            "some_extra_field": 42,
        })
        assert s.name == "My Squid"
        assert s.concurrency == 5
        assert s.last_run_at == "2025-01-01"
        assert s.last_run_is_done is True
        assert s.extra == {"some_extra_field": 42}


class TestRunModel:
    def test_from_dict_minimal(self):
        r = Run.from_dict({"id": "run1"})
        assert r.id == "run1"
        assert r.is_done is False
        assert r.export_done is False

    def test_from_dict_full(self):
        r = Run.from_dict({
            "id": "run1",
            "squid": "sq1",
            "status": "running",
            "is_done": True,
            "export_done": True,
            "duration": "00:01:23",
            "total_results": 10,
        })
        assert r.squid == "sq1"
        assert r.is_done is True
        assert r.export_done is True
        assert r.duration == "00:01:23"
        assert r.total_results == 10


class TestRunStatsModel:
    def test_from_dict(self):
        raw = {"is_done": True, "duration": "00:00:30", "total_results": 3, "extra": "val"}
        s = RunStats.from_dict(raw)
        assert s.is_done is True
        assert s.total_results == 3
        assert s.raw == raw


class TestScrapeResultModel:
    def test_fields(self):
        run = Run(id="r1")
        sr = ScrapeResult(data=[{"a": 1}], file_path="out.csv", run=run)
        assert len(sr.data) == 1
        assert sr.file_path == "out.csv"
        assert sr.run.id == "r1"


class TestCrawlers:
    def test_instagram_constant(self):
        assert Crawlers.INSTAGRAM_PROFILE_SCRAPER == "5c55ffbf10fbbc7ee63ebb2bcaa332b7"


# ---------------------------------------------------------------------------
# Client — session setup
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_session_headers(self, client):
        assert client.session.headers["Authorization"] == f"Token {FAKE_KEY}"
        assert "Content-Type" in client.session.headers

    def test_retry_adapter_mounted(self, client):
        adapter = client.session.get_adapter("https://api.lobstr.io")
        assert adapter.max_retries.total == client.MAX_RETRIES


# ---------------------------------------------------------------------------
# Client — low-level squid methods
# ---------------------------------------------------------------------------

class TestListSquids:
    def test_returns_all(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "data": [
                {"id": "s1", "crawler": "c1"},
                {"id": "s2", "crawler": "c2"},
            ]
        }))
        result = client.list_squids()
        assert len(result) == 2
        assert all(isinstance(s, Squid) for s in result)

    def test_filters_by_crawler(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "data": [
                {"id": "s1", "crawler": "c1"},
                {"id": "s2", "crawler": "c2"},
            ]
        }))
        result = client.list_squids(crawler_id="c1")
        assert len(result) == 1
        assert result[0].id == "s1"


class TestCreateSquid:
    def test_sends_crawler_id(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "id": "new-squid", "crawler": "c1"
        }))
        squid = client.create_squid("c1")
        assert squid.id == "new-squid"
        args, kwargs = client.session.request.call_args
        assert args == ("POST", f"{client.BASE_URL}/squids")
        assert kwargs["json"] == {"crawler": "c1"}


class TestUpdateSquid:
    def test_sends_name_and_concurrency(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "id": "s1", "crawler": "c1", "name": "Updated", "concurrency": 3
        }))
        squid = client.update_squid("s1", name="Updated", concurrency=3)
        assert squid.name == "Updated"
        assert squid.concurrency == 3
        _, kwargs = client.session.request.call_args
        assert kwargs["json"] == {"name": "Updated", "concurrency": 3}

    def test_sends_only_provided_fields(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "id": "s1", "crawler": "c1", "name": "Only Name"
        }))
        client.update_squid("s1", name="Only Name")
        _, kwargs = client.session.request.call_args
        assert kwargs["json"] == {"name": "Only Name"}


class TestDeleteSquid:
    def test_sends_delete(self, client):
        resp = _json_response({})
        client.session.request = MagicMock(return_value=resp)
        client.delete_squid("s1")
        args, _ = client.session.request.call_args
        assert args == ("DELETE", f"{client.BASE_URL}/squids/s1")


class TestEmptySquid:
    def test_sends_post_with_type(self, client):
        resp = _json_response({})
        client.session.request = MagicMock(return_value=resp)
        client.empty_squid("s1")
        _, kwargs = client.session.request.call_args
        assert kwargs["json"] == {"type": "url"}


# ---------------------------------------------------------------------------
# Client — tasks
# ---------------------------------------------------------------------------

class TestAddTasks:
    def test_formats_urls(self, client):
        client.session.request = MagicMock(return_value=_json_response({"ok": True}))
        result = client.add_tasks("sq1", ["https://a.com", "https://b.com"])
        _, kwargs = client.session.request.call_args
        assert kwargs["json"] == {
            "tasks": [{"url": "https://a.com"}, {"url": "https://b.com"}],
            "squid": "sq1",
        }

    def test_strips_whitespace_and_skips_empty(self, client):
        client.session.request = MagicMock(return_value=_json_response({"ok": True}))
        client.add_tasks("sq1", ["  https://a.com  ", "", "  "])
        _, kwargs = client.session.request.call_args
        assert kwargs["json"]["tasks"] == [{"url": "https://a.com"}]


# ---------------------------------------------------------------------------
# Client — runs
# ---------------------------------------------------------------------------

class TestStartRun:
    def test_returns_run(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "id": "run1", "squid": "sq1", "status": "running"
        }))
        run = client.start_run("sq1")
        assert isinstance(run, Run)
        assert run.id == "run1"


class TestGetRunStats:
    def test_returns_stats(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "is_done": True, "duration": "00:00:10", "total_results": 1
        }))
        stats = client.get_run_stats("run1")
        assert isinstance(stats, RunStats)
        assert stats.is_done is True
        assert stats.total_results == 1


class TestAbortRun:
    def test_sends_post(self, client):
        client.session.request = MagicMock(return_value=_json_response({"aborted": True}))
        result = client.abort_run("run1")
        assert result["aborted"] is True
        args, _ = client.session.request.call_args
        assert args == ("POST", f"{client.BASE_URL}/runs/run1/abort")


class TestListRuns:
    def test_returns_list_of_runs(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "data": [
                {"id": "r1", "squid": "sq1", "is_done": True},
                {"id": "r2", "squid": "sq1", "is_done": False},
            ]
        }))
        runs = client.list_runs("sq1")
        assert len(runs) == 2
        assert runs[0].is_done is True
        assert runs[1].is_done is False


class TestDownloadRun:
    def test_returns_s3_url(self, client):
        client.session.request = MagicMock(return_value=_json_response({
            "s3": "https://s3.example.com/results.csv"
        }))
        url = client.download_run("run1")
        assert url == "https://s3.example.com/results.csv"


# ---------------------------------------------------------------------------
# Client — error handling
# ---------------------------------------------------------------------------

class TestRequestErrors:
    def test_http_error_raises_lobstr_error(self, client):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 404
        resp.json.return_value = {"detail": "not found"}
        resp.text = "not found"
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)

        client.session.request = MagicMock(return_value=resp)
        with pytest.raises(LobstrError, match="404"):
            client.list_squids()

    def test_http_error_with_non_json_body(self, client):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 500
        resp.json.side_effect = ValueError("no json")
        resp.text = "Internal Server Error"
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)

        client.session.request = MagicMock(return_value=resp)
        with pytest.raises(LobstrError, match="Internal Server Error"):
            client.create_squid("c1")


# ---------------------------------------------------------------------------
# Client — high-level _poll_until_done
# ---------------------------------------------------------------------------

class TestPollUntilDone:
    @patch("lobstr.client.time.sleep", return_value=None)
    def test_polls_until_is_done(self, mock_sleep, client):
        stats_not_done = _json_response({"is_done": False, "total_results": 0})
        stats_done = _json_response({"is_done": True, "duration": "00:00:05", "total_results": 1})
        client.session.request = MagicMock(side_effect=[stats_not_done, stats_not_done, stats_done])

        result = client._poll_until_done("run1")
        assert result.is_done is True
        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# Client — high-level _wait_for_export
# ---------------------------------------------------------------------------

class TestWaitForExport:
    @patch("lobstr.client.time.sleep", return_value=None)
    def test_polls_until_export_done(self, mock_sleep, client):
        not_ready = _json_response({"data": [{"id": "r1", "squid": "sq1", "export_done": False}]})
        ready = _json_response({"data": [{"id": "r1", "squid": "sq1", "export_done": True}]})
        client.session.request = MagicMock(side_effect=[not_ready, ready])

        client._wait_for_export("sq1", "r1")
        assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# Client — high-level _download_csv
# ---------------------------------------------------------------------------

class TestDownloadCsv:
    def test_downloads_and_parses_csv(self, client, tmp_path, monkeypatch):
        # Make the download URL response
        client.session.request = MagicMock(return_value=_json_response({
            "s3": "https://s3.example.com/results.csv"
        }))

        # Build CSV content
        csv_content = "username,followers\ncristiano,600000000\nmessi,500000000\n"

        # Mock requests.get (the raw S3 download, not session.request)
        mock_s3_resp = MagicMock()
        mock_s3_resp.raise_for_status.return_value = None
        mock_s3_resp.iter_content.return_value = [csv_content.encode()]
        mock_s3_resp.__enter__ = lambda s: s
        mock_s3_resp.__exit__ = MagicMock(return_value=False)

        monkeypatch.chdir(tmp_path)
        with patch("lobstr.client.requests.get", return_value=mock_s3_resp):
            rows, filepath = client._download_csv("run1")

        assert len(rows) == 2
        assert rows[0]["username"] == "cristiano"
        assert rows[1]["followers"] == "500000000"
        assert filepath == "lobstr_results_run1.csv"
        assert (tmp_path / filepath).exists()

    def test_raises_on_empty_url(self, client):
        client.session.request = MagicMock(return_value=_json_response({"s3": ""}))
        with pytest.raises(LobstrError, match="No download URL"):
            client._download_csv("run1")


# ---------------------------------------------------------------------------
# Client — high-level scrape() (full flow)
# ---------------------------------------------------------------------------

class TestScrapeSync:
    @patch("lobstr.client.requests.get")
    @patch("lobstr.client.time.sleep", return_value=None)
    def test_full_sync_flow(self, mock_sleep, mock_requests_get, client, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        csv_content = "username,followers\ncristiano,600000000\n"

        # Build the sequence of session.request responses:
        # 1. create_squid
        # 2. add_tasks
        # 3. start_run
        # 4. get_run_stats (not done)
        # 5. get_run_stats (done)
        # 6. list_runs (export not done)
        # 7. list_runs (export done)
        # 8. download_run (get S3 URL)
        # 9. list_runs (refresh run info)
        responses = [
            _json_response({"id": "sq1", "crawler": "c1"}),                          # create_squid
            _json_response({"ok": True}),                                             # add_tasks
            _json_response({"id": "run1", "squid": "sq1", "status": "running"}),      # start_run
            _json_response({"is_done": False, "total_results": 0}),                   # get_run_stats
            _json_response({"is_done": True, "duration": "00:00:10", "total_results": 1}),  # get_run_stats
            _json_response({"data": [{"id": "run1", "squid": "sq1", "export_done": False}]}),  # list_runs
            _json_response({"data": [{"id": "run1", "squid": "sq1", "export_done": True}]}),   # list_runs
            _json_response({"s3": "https://s3.example.com/out.csv"}),                 # download_run
            _json_response({"data": [{"id": "run1", "squid": "sq1", "export_done": True, "is_done": True, "total_results": 1}]}),  # list_runs (refresh)
        ]
        client.session.request = MagicMock(side_effect=responses)

        # Mock S3 download
        mock_s3_resp = MagicMock()
        mock_s3_resp.raise_for_status.return_value = None
        mock_s3_resp.iter_content.return_value = [csv_content.encode()]
        mock_s3_resp.__enter__ = lambda s: s
        mock_s3_resp.__exit__ = MagicMock(return_value=False)
        mock_requests_get.return_value = mock_s3_resp

        result = client.scrape(
            crawler_id="c1",
            urls=["https://instagram.com/cristiano"],
        )

        assert isinstance(result, ScrapeResult)
        assert len(result.data) == 1
        assert result.data[0]["username"] == "cristiano"
        assert result.file_path == "lobstr_results_run1.csv"
        assert result.run.id == "run1"

    def test_sync_with_concurrency(self, client):
        """When concurrency != 1, update_squid should be called."""
        call_log = []

        def mock_request(method, url, **kwargs):
            call_log.append((method, url))
            if "squids" in url and method == "POST" and "empty" not in url and url.endswith("/squids"):
                return _json_response({"id": "sq1", "crawler": "c1"})
            if "squids/sq1" in url and method == "POST":
                return _json_response({"id": "sq1", "crawler": "c1", "concurrency": 3})
            if "tasks" in url:
                return _json_response({"ok": True})
            if method == "POST" and url.endswith("/runs"):
                return _json_response({"id": "run1", "squid": "sq1"})
            return _json_response({})

        client.session.request = MagicMock(side_effect=mock_request)

        # Use sync=False to avoid the polling loop
        run = client.scrape("c1", ["https://example.com"], sync=False, concurrency=3)
        assert isinstance(run, Run)
        # Verify update_squid was called (the second POST to squids/sq1)
        update_calls = [c for c in call_log if "squids/sq1" in c[1] and c[0] == "POST"]
        assert len(update_calls) == 1


class TestScrapeAsync:
    def test_returns_run_immediately(self, client):
        responses = [
            _json_response({"id": "sq1", "crawler": "c1"}),      # create_squid
            _json_response({"ok": True}),                         # add_tasks
            _json_response({"id": "run1", "squid": "sq1"}),       # start_run
        ]
        client.session.request = MagicMock(side_effect=responses)

        result = client.scrape("c1", ["https://example.com"], sync=False)
        assert isinstance(result, Run)
        assert result.id == "run1"
        # Only 3 API calls: create, tasks, start
        assert client.session.request.call_count == 3
