"""Create a draft (never send) in an Outlook / Microsoft 365 mailbox via Microsoft Graph."""
import base64

import httpx

from .config import settings

GRAPH = "https://graph.microsoft.com/v1.0"


class OutlookNotConfigured(Exception):
    pass


def _token(http: httpx.Client) -> str:
    resp = http.post(
        f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/token",
        data={"client_id": settings.graph_client_id, "client_secret": settings.graph_client_secret,
              "grant_type": "client_credentials", "scope": "https://graph.microsoft.com/.default"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Microsoft sign-in failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["access_token"]


def create_draft(subject: str, html_body: str, to: list[str], attachment_name: str, attachment: bytes,
                 transport=None) -> str:
    """Returns the Outlook web link of the new draft."""
    if not settings.graph_configured:
        raise OutlookNotConfigured("Set GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET and GRAPH_MAILBOX")
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
        resp = http.post(f"{GRAPH}/users/{settings.graph_mailbox}/messages", json=message,
                         headers={"Authorization": f"Bearer {token}"})
    if resp.status_code >= 400:
        raise RuntimeError(f"Graph error {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("webLink", "")
