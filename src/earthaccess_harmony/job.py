"""The remote Harmony job."""

from __future__ import annotations

import logging
from typing import Any

from earthaccess_plugin_spec import Asset, Job, JobStatus

__all__ = ["ORDER_URL", "STATUS_MAP", "HarmonyJob", "format_of"]

ORDER_URL = "https://harmony.earthdata.nasa.gov/workflow-ui/"

# Harmony API vocabulary; the single place to fix when it changes.
STATUS_MAP: dict[str, JobStatus] = {
    "accepted": JobStatus.PENDING,
    "running": JobStatus.RUNNING,
    "running_with_errors": JobStatus.RUNNING,
    "previewing": JobStatus.PAUSED,
    "paused": JobStatus.PAUSED,
    "successful": JobStatus.SUCCEEDED,
    "complete_with_errors": JobStatus.PARTIAL,
    "failed": JobStatus.FAILED,
    "canceled": JobStatus.CANCELED,
}

_LOGGER = logging.getLogger("earthaccess.plugins")

_EXTENSIONS = (
    (".nc4", "netcdf4"),
    (".nc", "netcdf4"),
    (".zarr", "zarr"),
    (".parquet", "geoparquet"),
    (".csv", "csv"),
    (".tif", "geotiff"),
)


def format_of(href: str) -> Any:
    base = href.split("?")[0].rstrip("/")
    for extension, fmt in _EXTENSIONS:
        if base.endswith(extension):
            return fmt
    return None


class HarmonyJob(Job):
    """A resumable, cancellable Harmony job. The only overrides are the three hooks."""

    resumable = True
    # capabilities ('cancel', 'pause', 'resume') are derived from the overrides.
    poll_every = 2.0

    def __init__(self, job_id: str, client: Any) -> None:
        super().__init__(plugin="harmony", output="assets", id=job_id)
        self._client = client

    def _poll(self) -> tuple[JobStatus, dict[str, Any]]:
        info = {k: v for k, v in self._client.status(self.id).items() if k != "request"}
        raw = info.get("status")
        status = STATUS_MAP.get(raw) if isinstance(raw, str) else None
        if status is None:
            # Unknown state: keep going, never raise.
            _LOGGER.warning("unknown Harmony status %r", raw)
            status = JobStatus.RUNNING
        info["order_url"] = ORDER_URL + str(self.id)
        return status, info

    def _fetch(self) -> list[Asset]:
        return [
            Asset(
                href=url,
                access="s3" if url.startswith("s3://") else "https",
                format=format_of(url),
            )
            for url in self._client.result_urls(self.id)
        ]

    def _cancel(self) -> bool:
        self._client.cancel(self.id)
        return True

    def pause(self) -> bool:
        self._client.pause(self.id)
        return True

    def resume(self) -> bool:
        self._client.resume(self.id)
        return True

    def skip_preview(self) -> bool:
        """Harmony-specific: accept a large request without a human preview.

        Like icepyx: a previewing/paused job cannot be skipped directly, but
        pausing and resuming it does the trick.
        """

        status = self._client.status(self.id).get("status")
        if status in ("paused", "previewing"):
            self._client.pause(self.id)
            self._client.resume(self.id)
        return True
