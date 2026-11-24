from gateway.security import generate_api_key, hash_api_key


def test_generated_keys_are_unique_and_prefixed():
    first, _, _ = generate_api_key("pepper")
    second, _, _ = generate_api_key("pepper")
    assert first != second
    assert first.startswith("gw_")


def test_hash_is_deterministic_and_depends_on_the_pepper():
    assert hash_api_key("k", "p1") == hash_api_key("k", "p1")
    assert hash_api_key("k", "p1") != hash_api_key("k", "p2")


def test_hash_does_not_reveal_the_key():
    key, prefix, key_hash = generate_api_key("pepper")
    assert key.startswith(prefix)
    assert key not in key_hash
    assert len(key_hash) == 64
