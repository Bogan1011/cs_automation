"""Apex One as a Service / Standard Endpoint Protection (Apex Central automation API) connector.

Auth: JWT (HS256) signed with the API key, claims appid / iat / version "V1" / checksum, where
checksum = base64(sha256(METHOD|lower(path?query)|canonical-API-headers|body)).
Application ID + API key come from the Apex Central / SEP console: Automation API Access Settings.
"""
import base64
import hashlib
import hmac
import json
import time

import httpx


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_jwt(app_id: str, api_key: str, method: str, raw_url: str, body: str = "", headers: str = "") -> str:
    checksum = base64.b64encode(hashlib.sha256(
        f"{method.upper()}|{raw_url.lower()}|{headers}|{body}".encode()).digest()).decode()
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"appid": app_id, "iat": time.time(), "version": "V1", "checksum": checksum}
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(api_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)


class ApexClient:
    def __init__(self, base_url: str, app_id: str, api_key: str, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.app_id, self.api_key = app_id, api_key
        self._http = httpx.Client(timeout=90, transport=transport)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def request(self, method: str, path_and_query: str, body: dict | None = None) -> dict:
        body_s = json.dumps(body) if body is not None else ""
        token = make_jwt(self.app_id, self.api_key, method, path_and_query, body_s)
        r = self._http.request(method, self.base_url + path_and_query, content=body_s or None,
                               headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json;charset=utf-8"})
        if r.status_code >= 400:
            raise RuntimeError(f"Apex {method} {path_and_query}: {r.status_code} {r.text[:300]}")
        return r.json()

    def product_agents(self) -> list[dict]:
        data = self.request("GET", "/WebApp/API/AgentResource/ProductAgents")
        content = data.get("result_content", data)
        return content if isinstance(content, list) else [content]


# Paths tried by `python -m app.cli apex-probe`. Only ProductAgents is confirmed in public docs;
# the others are checked so we can see which ones this tenant exposes.
PROBE_PATHS = [
    "/WebApp/API/AgentResource/ProductAgents",
    "/WebApp/API/ServerResource/ProductServers",
    "/WebApp/API/v1/Endpoints",
    "/WebApp/API/ComponentResource/Components",
]
