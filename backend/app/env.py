"""Load backend/.env into the process environment. Real environment variables win."""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))
