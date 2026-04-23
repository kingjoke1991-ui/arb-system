import os
import sys

# Ensure tests don't accidentally pick up the user's real .env
os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://x:x@localhost/none")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# Make the project importable when running pytest from root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
