from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Squid:
    """Represents a Lobstr squid (a configured scraper instance)."""

    id: str
    crawler: str
    name: str = ""
    concurrency: int = 1
    last_run_at: Optional[str] = None
    last_run_is_done: Optional[bool] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Squid:
        known = {"id", "crawler", "name", "concurrency", "last_run_at", "last_run_is_done"}
        return cls(
            id=data["id"],
            crawler=data.get("crawler", ""),
            name=data.get("name", ""),
            concurrency=data.get("concurrency", 1),
            last_run_at=data.get("last_run_at"),
            last_run_is_done=data.get("last_run_is_done"),
            extra={k: v for k, v in data.items() if k not in known},
        )


@dataclass
class Run:
    """Represents a Lobstr run."""

    id: str
    squid: str = ""
    status: str = ""
    is_done: bool = False
    export_done: bool = False
    duration: Optional[str] = None
    total_results: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Run:
        known = {"id", "squid", "status", "is_done", "export_done", "duration", "total_results"}
        return cls(
            id=data["id"],
            squid=data.get("squid", ""),
            status=data.get("status", ""),
            is_done=data.get("is_done", False),
            export_done=data.get("export_done", False),
            duration=data.get("duration"),
            total_results=data.get("total_results"),
            extra={k: v for k, v in data.items() if k not in known},
        )


@dataclass
class RunStats:
    """Wraps the /runs/{id}/stats response."""

    is_done: bool = False
    duration: Optional[str] = None
    total_results: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RunStats:
        return cls(
            is_done=data.get("is_done", False),
            duration=data.get("duration"),
            total_results=data.get("total_results", 0),
            raw=data,
        )


@dataclass
class ScrapeResult:
    """Result of a synchronous scrape: parsed CSV data + file path."""

    data: List[Dict[str, Any]]
    file_path: str
    run: Run


class Crawlers:
    """Known crawler hash IDs."""

    INSTAGRAM_PROFILE_SCRAPER = "5c55ffbf10fbbc7ee63ebb2bcaa332b7"
