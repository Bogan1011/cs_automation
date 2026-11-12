import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tempfile
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("MASTER_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DASHBOARD_PASSWORD", "pw")
os.environ.setdefault("ENABLE_SCHEDULER", "0")
