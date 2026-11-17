"""Microsoft Graph for Outlook: create report drafts (never send) and read Trend case emails.

Two ways to sign in:
- App-only (GRAPH_CLIENT_SECRET set): needs an admin to grant application permission Mail.ReadWrite.
- Delegated (no secret): any user can register the app; sign in once with `python -m app.cli graph-login`
  (device code). The refresh token is stored encrypted in the database and the app acts as that user,
  on that user's own mailbox only.
"""
import base64
import time

import httpx

from .config import settings

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "https://graph.microsoft.com/Mail.ReadWrite offline_access"
TOKEN_KEY = "graph_refresh_token"


class OutlookNotConfigured(Exception):
    pass


def _login_url(path: str) -> str:
    return f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/{path}"


def delegated() -> bool:
    return bool(settings.graph_tenant_id and settings.graph_client_id and not settings.graph_client_secret)


def _stored_refresh() -> str | None:
    from . import crypto
    from .db import SessionLocal, SyncState
    with SessionLocal() as s:
        row = s.get(SyncState, TOKEN_KEY)
        return crypto.decrypt(row.value) if row and row.value else None


def _store_refresh(token: str) -> None:
    from . import crypto
    from .db import SessionLocal, SyncState
    with SessionLocal() as s:
        s.merge(SyncState(key=TOKEN_KEY, value=crypto.encrypt(token)))
        s.commit()


def configured() -> bool:
    if delegated():
        try:
            return bool(_stored_refresh())
        except Exception:
            return False
    return settings.graph_configured


def mailbox_path(mailbox: str | None = None) -> str:
    """'/me' when signed in as the user, else '/users/{mailbox}'."""
    return "/me" if delegated() else f"/users/{mailbox or settings.graph_mailbox}"


def _token(http: httpx.Client) -> str:
    if delegated():
        refresh = _stored_refresh()
        if not refresh:
            raise OutlookNotConfigured("Not signed in to Microsoft 365. Run: python -m app.cli graph-login")
        resp = http.post(_login_url("token"), data={"client_id": settings.graph_client_id, "grant_type": "refresh_token",
                                                     "refresh_token": refresh, "scope": SCOPES})
        if resp.status_code >= 400:
            raise RuntimeError(f"Microsoft sign-in expired or was revoked ({resp.status_code}); run graph-login again: {resp.text[:200]}")
        data = resp.json()
        if data.get("refresh_token") and data["refresh_token"] != refresh:
            _store_refresh(data["refresh_token"])  # Microsoft rotates refresh tokens
        return data["access_token"]
    resp = http.post(_login_url("token"), data={
        "client_id": settings.graph_client_id, "client_secret": settings.graph_client_secret,
        "grant_type": "client_credentials", "scope": "https://graph.microsoft.com/.default"})
    if resp.status_code >= 400:
        raise RuntimeError(f"Microsoft sign-in failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["access_token"]


def device_login(transport=None, say=print) -> str:
    """Interactive one-time sign-in (device code). Returns the signed-in user's address."""
    if not delegated():
        raise OutlookNotConfigured("Set GRAPH_TENANT_ID and GRAPH_CLIENT_ID (and leave GRAPH_CLIENT_SECRET empty)")
    with httpx.Client(transport=transport, timeout=30) as http:
        r = http.post(_login_url("devicecode"), data={"client_id": settings.graph_client_id, "scope": SCOPES})
        if r.status_code >= 400:
            raise RuntimeError(f"Device sign-in failed ({r.status_code}): {r.text[:300]}")
        dc = r.json()
        say(dc.get("message") or f"Open {dc['verification_uri']} and enter {dc['user_code']}")
        deadline = time.time() + int(dc.get("expires_in", 900))
        while time.time() < deadline:
            time.sleep(int(dc.get("interval", 5)))
            t = http.post(_login_url("token"), data={"client_id": settings.graph_client_id, "device_code": dc["device_code"],
                                                     "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            data = t.json()
            if t.status_code == 200:
                _store_refresh(data["refresh_token"])
                me = http.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {data['access_token']}"}).json()
                return me.get("mail") or me.get("userPrincipalName") or "signed in"
            if data.get("error") not in ("authorization_pending", "slow_down"):
                raise RuntimeError(f"Sign-in failed: {data.get('error_description', data)[:300]}")
    raise RuntimeError("Sign-in timed out; run graph-login again")


def create_draft(subject: str, html_body: str, to: list[str], attachment_name: str, attachment: bytes,
                 transport=None) -> str:
    """Returns the Outlook web link of the new draft."""
    if not configured():
        raise OutlookNotConfigured("Outlook not connected: see README section 3")
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to if a],
        "attachments": [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment_name,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(attachment).decode(),
        }],
    }
    with httpx.Client(transport=transport, timeout=60) as http:
        token = _token(http)
        resp = http.post(f"{GRAPH}{mailbox_path()}/messages", json=message,
                         headers={"Authorization": f"Bearer {token}"})
    if resp.status_code >= 400:
        raise RuntimeError(f"Graph error {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("webLink", "")
