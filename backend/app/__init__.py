import os
from pathlib import Path

_env = Path(__file__).resolve().parents[2] / ".env"
if _env.exists():
    for _l in _env.read_text().splitlines():
        if "=" in _l and not _l.strip().startswith("#"):
            _k, _v = _l.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())
