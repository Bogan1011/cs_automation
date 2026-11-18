import os
from dataclasses import dataclass, field

# Vision One API base URL per region (portal.<region>.xdr.trendmicro.com -> api.<region>.xdr.trendmicro.com)
REGIONS = {
    "us": "https://api.xdr.trendmicro.com",
    "eu": "https://api.eu.xdr.trendmicro.com",
    "uk": "https://api.uk.xdr.trendmicro.com",
    "jp": "https://api.xdr.trendmicro.co.jp",
    "sg": "https://api.sg.xdr.trendmicro.com",
    "au": "https://api.au.xdr.trendmicro.com",
    "in": "https://api.in.xdr.trendmicro.com",
    "mea": "https://api.mea.xdr.trendmicro.com",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///./data/dashboard.db"))
    master_key: str = field(default_factory=lambda: _env("MASTER_KEY"))
    dashboard_user: str = field(default_factory=lambda: _env("DASHBOARD_USER", "admin"))
    dashboard_password: str = field(default_factory=lambda: _env("DASHBOARD_PASSWORD"))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET", "dev-only-secret"))
    poll_interval: int = field(default_factory=lambda: int(_env("POLL_INTERVAL_SECONDS", "300")))
    retention_days: int = field(default_factory=lambda: int(_env("RETENTION_DAYS", "90")))
    stale_days: int = field(default_factory=lambda: int(_env("STALE_DAYS", "7")))
    # Exact pattern versions need one API call per endpoint, so they refresh on a rolling basis:
    # each poll re-fetches up to DETAIL_BATCH endpoints whose cached versions are older than DETAIL_TTL_MINUTES.
    detail_ttl_minutes: int = field(default_factory=lambda: int(_env("DETAIL_TTL_MINUTES", "60")))
    detail_batch: int = field(default_factory=lambda: int(_env("DETAIL_BATCH", "300")))
    detail_workers: int = field(default_factory=lambda: int(_env("DETAIL_WORKERS", "6")))
    graph_tenant_id: str = field(default_factory=lambda: _env("GRAPH_TENANT_ID"))
    graph_client_id: str = field(default_factory=lambda: _env("GRAPH_CLIENT_ID"))
    graph_client_secret: str = field(default_factory=lambda: _env("GRAPH_CLIENT_SECRET"))
    graph_mailbox: str = field(default_factory=lambda: _env("GRAPH_MAILBOX"))
    # Trend support cases are read from this mailbox (defaults to GRAPH_MAILBOX). Needs Graph Mail.Read.
    cases_mailbox: str = field(default_factory=lambda: _env("CASES_MAILBOX"))
    cases_backfill_days: int = field(default_factory=lambda: int(_env("CASES_BACKFILL_DAYS", "120")))
    # More sign-ins for the team: "akshay:password1,colleague:password2". DASHBOARD_USER keeps working too.
    dashboard_users_raw: str = field(default_factory=lambda: _env("DASHBOARD_USERS"))
    # Public Trend Business Success pages to read for the latest articles (comma-separated).
    article_sources_raw: str = field(default_factory=lambda: _env(
        "TREND_ARTICLE_URLS", "https://success.trendmicro.com/en-US/product/?name=trend-vision-one"))
    article_interval_minutes: int = field(default_factory=lambda: int(_env("ARTICLE_INTERVAL_MINUTES", "180")))
    # Our own team's mail domains: their replies in a case thread count as the solution side (with Trend's).
    team_domains_raw: str = field(default_factory=lambda: _env("TEAM_EMAIL_DOMAINS", "eventussecurity.com"))
    enable_scheduler: bool = field(default_factory=lambda: _env("ENABLE_SCHEDULER", "1") == "1")

    @property
    def users(self) -> dict[str, str]:
        """username -> password for everyone who can sign in."""
        out = {}
        if self.dashboard_user and self.dashboard_password:
            out[self.dashboard_user] = self.dashboard_password
        for pair in self.dashboard_users_raw.split(","):
            name, sep, pw = pair.strip().partition(":")
            if sep and name.strip() and pw:
                out[name.strip()] = pw
        return out

    @property
    def team_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.team_domains_raw.split(",") if d.strip()]

    @property
    def article_sources(self) -> list[str]:
        return [u.strip() for u in self.article_sources_raw.split(",") if u.strip()]

    @property
    def graph_configured(self) -> bool:
        return all([self.graph_tenant_id, self.graph_client_id, self.graph_client_secret, self.graph_mailbox])


settings = Settings()
