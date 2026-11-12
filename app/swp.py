"""Server & Workload Protection (Deep Security / Workload Security API) connector.

Used only for exact pattern / rule update versions per computer, which the Vision One
endpoint API does not expose. Needs a separate API key created in the SWP console
(Administration > User Management > API Keys).
"""
import httpx

PAGE = 1000


class SWPClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api"):
            self.base_url += "/api"
        self._key = api_key
        self._http = httpx.Client(timeout=90, transport=transport)
        self._auth = {"Authorization": f"ApiKey {api_key}"}

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _request(self, method: str, path: str, **kw) -> dict:
        url = self.base_url + path
        for attempt in range(2):
            headers = {"api-version": "v1", "Content-Type": "application/json", **self._auth}
            r = self._http.request(method, url, headers=headers, **kw)
            if r.status_code >= 400 and attempt == 0 and "Authorization" in self._auth:
                # Legacy-style keys use the api-secret-key header; the ApiKey header can answer 401/403
                # or even 500 for them (seen on Bandhan's SWP tenant).
                self._auth = {"api-secret-key": self._key}
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"SWP {method} {path}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"SWP {method} {path}: authentication failed with both header styles")

    def list_computers(self, expand: tuple[str, ...] = ("computerStatus", "securityUpdates")) -> list[dict]:
        """Pages through POST /computers/search ordered by ID."""
        out, last_id = [], 0
        params = [("expand", e) for e in expand]
        while True:
            body = {"maxItems": PAGE, "searchCriteria": [{"idValue": last_id, "idTest": "greater-than"}],
                    "sortByObjectID": True}
            data = self._request("POST", "/computers/search", params=params, json=body)
            batch = data.get("computers", [])
            out.extend(batch)
            if len(batch) < PAGE:
                return out
            last_id = batch[-1]["ID"]


def computer_key(c: dict) -> list[str]:
    """Names a computer may be matched on against Vision One endpoint names."""
    names = [c.get("hostName"), c.get("displayName")]
    keys = []
    for n in names:
        if n:
            n = str(n).strip().lower()
            keys += [n, n.split(".")[0]]
    return keys
