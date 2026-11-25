import json

import pytest
from pydantic import ValidationError

from gateway.providers.config import load_provider_configs


def write(tmp_path, data):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(data))
    return path


def test_loads_a_valid_file(tmp_path):
    path = write(
        tmp_path,
        {
            "providers": [
                {
                    "name": "primary",
                    "base_url": "https://a/v1",
                    "api_key_env": "A_KEY",
                    "model": "m1",
                },
                {"name": "backup", "base_url": "https://b/v1"},
            ]
        },
    )
    configs = load_provider_configs(path)
    assert [c.name for c in configs] == ["primary", "backup"]
    assert configs[0].api_key_env == "A_KEY"
    assert configs[1].api_key_env is None


@pytest.mark.parametrize(
    "data",
    [
        {"providers": []},
        {"providers": [{"name": "a", "base_url": "x"}, {"name": "a", "base_url": "y"}]},
        {"providers": [{"name": "Bad Name", "base_url": "x"}]},
    ],
    ids=["empty", "duplicate-names", "bad-name"],
)
def test_invalid_files_are_rejected(tmp_path, data):
    with pytest.raises(ValidationError):
        load_provider_configs(write(tmp_path, data))
