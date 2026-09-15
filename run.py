#!/usr/bin/env python3
"""
Wrapper: loads .env into os.environ, then runs main.py directly.
Usage: python3 run.py   (or: venv/bin/python run.py)
"""
import os
import sys
from pathlib import Path

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in os.environ:
                os.environ[key] = val
            elif key:
                os.environ[key] = val

print(f"[run.py] PORT={os.environ.get('PORT', '5000')}")
print(f"[run.py] WEBAPP_URL={os.environ.get('WEBAPP_URL', '(not set)')}")
print(f"[run.py] MONOBANK_TOKEN={'set' if os.environ.get('MONOBANK_TOKEN') else 'MISSING'}")
print(f"[run.py] FAZERCARDS_API_KEY={'set' if os.environ.get('FAZERCARDS_API_KEY') else 'MISSING'}")
print(f"[run.py] TOKEN={'set' if os.environ.get('TELEGRAM_BOT_TOKEN') else 'MISSING'}")
print()

# Replace this process with python running main.py directly
# This ensures __name__ == "__main__" and avoids weak reference issues
_main_path = str(Path(__file__).parent / "main.py")
os.execvp(sys.executable, [sys.executable, _main_path])
