"""Entry point: `uvicorn gateway.main:app`."""

from gateway.app import create_app

app = create_app()
