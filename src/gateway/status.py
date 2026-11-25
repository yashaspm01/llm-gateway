"""Describe the health of the provider stack, for /admin/providers."""


def provider_status(provider: object) -> list[dict[str, object]]:
    describe = getattr(provider, "status", None)
    if callable(describe):
        return list(describe())
    return [{"name": getattr(provider, "name", "unknown"), "state": "n/a"}]
