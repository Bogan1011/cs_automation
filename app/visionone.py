"""Minimal Trend Vision One public API (v3.0) client.

Uses a per-client API key (Administration > API Keys in each Vision One console).
Docs: https://automation.trendmicro.com/xdr/api-v3
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

from .config import REGIONS


class VisionOneError(Exception):
    pass


class VisionOneClient:
    def __init__(self, api_key: str, region: str, transport: httpx.BaseTransport | None = None, timeout: float = 60):
        if region not in REGIONS:
            raise ValueError(f"Unknown region {region!r}; expected one of {', '.join(REGIONS)}")
        self.base_url = REGIONS[region]
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        for attempt in range(4):
            resp = self._http.get(url, params=params, headers=headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt * 5))
                time.sleep(min(wait, 60))
                continue
            if resp.status_code in (401, 403):
                raise VisionOneError(f"{resp.status_code} on {url}: API key invalid, expired, or missing permission")
            if resp.status_code >= 400:
                raise VisionOneError(f"{resp.status_code} on {url}: {resp.text[:300]}")
            return resp.json()
        raise VisionOneError(f"Gave up on {url} after repeated throttling/server errors")

    def _paginate(self, path: str, params: dict | None = None, headers: dict | None = None,
                  max_pages: int = 200) -> Iterator[dict]:
        data = self._get(path, params=params, headers=headers)
        for _ in range(max_pages):
            yield from data.get("items", [])
            next_link = data.get("nextLink")
            if not next_link:
                return
            # nextLink is an absolute URL that already carries the paging token
            data = self._get(next_link, headers=headers)

    # --- Endpoint Security: endpoint inventory incl. Standard Endpoint Protection / Server & Workload Protection
    def list_endpoints(self) -> list[dict]:
        return list(self._paginate("/v3.0/endpointSecurity/endpoints", params={"top": 200}))

    # --- Workbench alerts (for the report and the alert counters)
    def list_alerts(self, days: int = 7) -> list[dict]:
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return list(self._paginate("/v3.0/workbench/alerts", params={"startDateTime": start}))
