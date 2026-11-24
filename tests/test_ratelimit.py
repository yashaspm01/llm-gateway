from gateway.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make() -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return RateLimiter(clock=clock), clock


def test_allows_a_burst_up_to_capacity_then_denies():
    limiter, _ = make()
    assert [limiter.check(1, 3) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.check(1, 3) > 0


def test_refills_over_time():
    limiter, clock = make()
    for _ in range(3):
        limiter.check(1, 3)
    assert limiter.check(1, 3) > 0
    clock.now += 20  # 3 per minute = 0.05 tokens per second, so 20s earns one token
    assert limiter.check(1, 3) == 0.0


def test_retry_after_says_how_long_to_wait():
    limiter, _ = make()
    for _ in range(60):
        limiter.check(1, 60)  # 60 per minute = 1 token per second
    assert limiter.check(1, 60) == 1.0


def test_keys_are_independent():
    limiter, _ = make()
    assert limiter.check(1, 1) == 0.0
    assert limiter.check(1, 1) > 0
    assert limiter.check(2, 1) == 0.0


def test_bucket_never_holds_more_than_capacity():
    limiter, clock = make()
    limiter.check(1, 3)
    clock.now += 10_000
    assert [limiter.check(1, 3) for _ in range(3)] == [0.0, 0.0, 0.0]
    assert limiter.check(1, 3) > 0


def test_lowering_the_limit_takes_effect_immediately():
    limiter, _ = make()
    assert limiter.check(1, 60) == 0.0
    assert limiter.check(1, 2) == 0.0
    assert limiter.check(1, 2) == 0.0
    assert limiter.check(1, 2) > 0
