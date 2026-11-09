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
    graph_tenant_id: str = field(default_factory=lambda: _env("GRAPH_TENANT_ID"))
    graph_client_id: str = field(default_factory=lambda: _env("GRAPH_CLIENT_ID"))
    graph_client_secret: str = field(default_factory=lambda: _env("GRAPH_CLIENT_SECRET"))
    graph_mailbox: str = field(default_factory=lambda: _env("GRAPH_MAILBOX"))
    enable_scheduler: bool = field(default_factory=lambda: _env("ENABLE_SCHEDULER", "1") == "1")

    @property
    def graph_configured(self) -> bool:
        return all([self.graph_tenant_id, self.graph_client_id, self.graph_client_secret, self.graph_mailbox])


settings = Settings()
