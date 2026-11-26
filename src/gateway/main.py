"""Entry point: `uvicorn gateway.main:app`."""

from gateway.app import create_app
from gateway.config import get_settings
from gateway.logging_setup import setup_logging

_settings = get_settings()
setup_logging(_settings.log_level, _settings.log_format)
app = create_app(settings=_settings)
