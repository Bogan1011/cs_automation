"""One click: make a OneDrive folder for a client's diagnostic logs and a link the client can upload to.

Two ways to reach OneDrive, the first that is set up wins:
1. rclone (no Entra app needed): sign in once with `rclone config`, a remote named ONEDRIVE_RCLONE_REMOTE.
   Gives an anyone-with-the-link edit link, which lets the client upload from the browser.
2. Microsoft Graph with the console's own Entra app, described below.

Signs in as the engineer (delegated Files.ReadWrite, device code once with `python -m app.cli onedrive-login`),
using the same Entra app as Outlook (GRAPH_TENANT_ID / GRAPH_CLIENT_ID, no secret). Only touches folders under
ONEDRIVE_LOG_ROOT in that person's own OneDrive.

Link order: an upload-only link ("Request files": the client can add files but not see or change anything), then
an anyone-with-the-link edit link if the tenant blocks upload-only links. If the tenant allows neither, the folder is
still made and its OneDrive address is returned so it can be shared by hand.
"""
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from . import outlook
from .config import settings

SCOPES = "https://graph.microsoft.com/Files.ReadWrite offline_access User.Read"
TOKEN_KEY = "onedrive_refresh_token"
GRAPH = outlook.GRAPH


class NotConnected(Exception):
    pass


def _safe(part: str) -> str:
    """OneDrive forbids " * : < > ? / \\ | and trailing dots or spaces in names."""
    part = re.sub(r'[\"*:<>?/\\\\|#%]+', "-", (part or "").strip())
    return re.sub(r"\s+", " ", part).strip(" .-")[:80]


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_client(client: str, existing: list[str]) -> str:
    """Use the client's existing OneDrive folder when the name matches it loosely ("Bandhan Life" -> BandhanLife)."""
    k = _key(client)
    return next((f for f in existing if _key(f) == k), "") if k else ""


def folder_path(client: str, host: str, trend_case: str = "", when: datetime | None = None,
                existing: list[str] | None = None) -> list[str]:
    when = when or datetime.now()
    leaf = " ".join(x for x in (when.strftime("%Y-%m-%d"), _safe(host), _safe(trend_case)) if x)
    folder = match_client(client, existing or []) or _safe(client) or "Other"
    return [p for p in (_safe(settings.onedrive_root) or "logs", folder, leaf) if p]


def _stored() -> str | None:
    from . import crypto
    from .db import SessionLocal, SyncState
    try:
        with SessionLocal() as s:
            row = s.get(SyncState, TOKEN_KEY)
            return crypto.decrypt(row.value) if row and row.value else None
    except Exception:
        return None


def _store(token: str) -> None:
    from . import crypto
    from .db import SessionLocal, SyncState
    with SessionLocal() as s:
        s.merge(SyncState(key=TOKEN_KEY, value=crypto.encrypt(token)))
        s.commit()


def app_ready() -> bool:
    return bool(settings.graph_tenant_id and settings.graph_client_id)


def connected() -> bool:
    return app_ready() and bool(_stored())


def _token(http: httpx.Client) -> str:
    refresh = _stored()
    if not app_ready() or not refresh:
        raise NotConnected("OneDrive is not connected yet. Run: python -m app.cli onedrive-login")
    r = http.post(outlook._login_url("token"), data={"client_id": settings.graph_client_id, "grant_type": "refresh_token",
                                                   "refresh_token": refresh, "scope": SCOPES})
    if r.status_code >= 400:
        raise NotConnected(f"The OneDrive sign-in expired or was revoked; run onedrive-login again ({r.status_code})")
    data = r.json()
    if data.get("refresh_token") and data["refresh_token"] != refresh:
        _store(data["refresh_token"])
    return data["access_token"]


def device_login(transport=None, say=print) -> str:
    """One-time sign-in. Returns the signed-in account."""
    if not app_ready():
        raise NotConnected("Set GRAPH_TENANT_ID and GRAPH_CLIENT_ID in .env first (the Entra app IT registers)")
    import time
    with httpx.Client(transport=transport, timeout=30) as http:
        r = http.post(outlook._login_url("devicecode"), data={"client_id": settings.graph_client_id, "scope": SCOPES})
        if r.status_code >= 400:
            raise RuntimeError(f"Device sign-in failed ({r.status_code}): {r.text[:300]}")
        dc = r.json()
        say(dc.get("message") or f"Open {dc['verification_uri']} and enter {dc['user_code']}")
        deadline = time.time() + int(dc.get("expires_in", 900))
        while time.time() < deadline:
            time.sleep(int(dc.get("interval", 5)))
            t = http.post(outlook._login_url("token"), data={"client_id": settings.graph_client_id, "device_code": dc["device_code"],
                                                           "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            data = t.json()
            if t.status_code == 200:
                _store(data["refresh_token"])
                me = http.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {data['access_token']}"}).json()
                return me.get("userPrincipalName") or me.get("mail") or "signed in"
            if data.get("error") not in ("authorization_pending", "slow_down"):
                raise RuntimeError(f"Sign-in failed: {str(data.get('error_description', data))[:300]}")
    raise RuntimeError("Sign-in timed out; run onedrive-login again")


def _ensure_folder(http: httpx.Client, parts: list[str]) -> dict:
    parent = "/me/drive/root"
    item: dict = {}
    for i, name in enumerate(parts):
        here = "/".join(quote(p) for p in parts[: i + 1])
        got = http.get(f"{GRAPH}/me/drive/root:/{here}")
        if got.status_code == 200:
            item = got.json()
        else:
            made = http.post(f"{GRAPH}{parent}/children",
                             json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
            if made.status_code == 409:
                made = http.get(f"{GRAPH}/me/drive/root:/{here}")
            if made.status_code >= 400:
                raise RuntimeError(f"OneDrive could not create '{name}' ({made.status_code}): {made.text[:200]}")
            item = made.json()
        parent = f"/me/drive/items/{item['id']}"
    return item


def _share(http: httpx.Client, item_id: str) -> tuple[str, str]:
    for kind, body in (("upload only", {"type": "createOnly", "scope": "anonymous"}),
                       ("anyone can edit", {"type": "edit", "scope": "anonymous"})):
        r = http.post(f"{GRAPH}/me/drive/items/{item_id}/createLink", json=body)
        if r.status_code < 300:
            return r.json().get("link", {}).get("webUrl", ""), kind
    return "", ""


# ---------- rclone ----------

def _rclone_conf() -> Path:
    return Path(os.environ.get("RCLONE_CONFIG") or Path.home() / ".config" / "rclone" / "rclone.conf")


def rclone_ready() -> bool:
    if not shutil.which("rclone"):
        return False
    try:
        return f"[{settings.onedrive_remote}]" in _rclone_conf().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=90)


_folders_cache: dict = {"at": 0.0, "names": []}


def client_folders(run=None, max_age: float = 300) -> list[str]:
    """Client folders already under the logs root in OneDrive (rclone only; empty when not set up)."""
    import time
    if run is None:
        if not rclone_ready():
            return []
        if time.time() - _folders_cache["at"] < max_age:
            return list(_folders_cache["names"])
    r = (run or _run)(["lsf", "--dirs-only", f"{settings.onedrive_remote}:{_safe(settings.onedrive_root) or 'logs'}"])
    names = sorted((ln.rstrip("/") for ln in r.stdout.splitlines() if ln.strip()), key=str.lower) if r.returncode == 0 else []
    if run is None:
        _folders_cache.update(at=time.time(), names=names)
    return names


def _rclone_create(parts: list[str], run=_run) -> dict:
    target = f"{settings.onedrive_remote}:{'/'.join(parts)}"
    r = run(["mkdir", target])
    if r.returncode:
        raise RuntimeError("rclone could not create the folder: " + _rclone_err(r))
    url, kind = "", ""
    r = run(["link", target, "--onedrive-link-scope", "anonymous", "--onedrive-link-type", "edit"])
    if r.returncode == 0 and r.stdout.strip():
        url, kind = r.stdout.strip().splitlines()[-1], "anyone can upload"
    own = run(["link", target, "--onedrive-link-scope", "organization", "--onedrive-link-type", "view"])
    folder = own.stdout.strip().splitlines()[-1] if own.returncode == 0 and own.stdout.strip() else ""
    return {"path": "/".join(parts), "folder_url": folder, "upload_url": url, "link_kind": kind}


def _rclone_err(r) -> str:
    text = (r.stderr or r.stdout or "").strip().splitlines()
    msg = text[-1] if text else f"exit {r.returncode}"
    if "token" in msg.lower() and ("expired" in msg.lower() or "invalid" in msg.lower()):
        msg += " (sign in again with: rclone config reconnect " + settings.onedrive_remote + ":)"
    return msg[:300]


def ready() -> bool:
    return rclone_ready() or connected()


def create_log_folder(client: str, host: str, trend_case: str = "", transport=None, run=None) -> dict:
    """Make (or reuse) <root>/<client>/<date> <host> <case> and return {path, folder_url, upload_url, link_kind}."""
    if run is not None or (transport is None and rclone_ready()):
        existing = client_folders(run) if run is not None else client_folders()
        parts = folder_path(client, host, trend_case, existing=existing)
        out = _rclone_create(parts, run or _run)
        if parts[1] not in existing:
            _folders_cache["at"] = 0.0          # a new client folder: list again next time
        return out
    parts = folder_path(client, host, trend_case)
    with httpx.Client(transport=transport, timeout=30) as http:
        http.headers["Authorization"] = f"Bearer {_token(http)}"
        item = _ensure_folder(http, parts)
        url, kind = _share(http, item["id"])
    return {"path": "/".join(parts), "folder_url": item.get("webUrl", ""), "upload_url": url, "link_kind": kind}


def message(client: str, host: str, url: str, trend_case: str = "") -> str:
    case = f" for Trend case {trend_case}" if trend_case else ""
    return (f"Hi {client or 'team'},\n\nPlease upload the diagnostic logs from {host}{case} here:\n{url}\n\n"
            "You only need to open the link and drop the files in; no sign-in is needed. "
            "Let us know once the upload has finished.\n\nThanks,\nEventus Customer Success")
