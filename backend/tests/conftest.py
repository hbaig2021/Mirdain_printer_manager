"""Shared pytest-only configuration for the local API test client."""

import os


# Starlette's TestClient uses this synthetic Host by default. Production
# defaults remain limited to actual loopback names and addresses.
os.environ.setdefault("MIRDAIN_ALLOWED_HOSTS", "testserver")
