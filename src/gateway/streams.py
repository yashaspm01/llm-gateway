"""Small helpers for working with async streams."""


async def close_stream(stream: object) -> None:
    """Close an async generator if it has an aclose(). Safe to call on anything."""
    closer = getattr(stream, "aclose", None)
    if closer is not None:
        await closer()
