"""The provider list, loaded from a JSON file. It names env vars and never holds secrets."""

import json
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator


class ProviderConfig(BaseModel):
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_-]+$")
    base_url: str = Field(min_length=1)
    api_key_env: str | None = None  # the NAME of the environment variable holding the key
    model: str | None = None  # replaces the client's model name when calling this provider


class ProvidersFile(BaseModel):
    providers: list[ProviderConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _names_must_be_unique(self) -> Self:
        names = [p.name for p in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("provider names must be unique")
        return self


def load_provider_configs(path: Path) -> list[ProviderConfig]:
    return ProvidersFile.model_validate(json.loads(path.read_text(encoding="utf-8"))).providers
