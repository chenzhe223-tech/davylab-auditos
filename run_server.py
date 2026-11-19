"""Deployment entry point: sandbox exec does NOT expand $PORT, so read the
PORT environment variable here. Run from the repository root."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("auditos.main:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", "8000")))
