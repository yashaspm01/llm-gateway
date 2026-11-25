import pytest

from gateway.circuit import CircuitBreaker, State


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(threshold=3, recovery=30.0):
    clock = FakeClock()
    breaker = CircuitBreaker(
        "t", failure_threshold=threshold, recovery_timeout_s=recovery, clock=clock
    )
    return breaker, clock


def fail(breaker, times):
    for _ in range(times):
        assert breaker.allow()
        breaker.record_failure()


def test_starts_closed_and_allows_calls():
    breaker, _ = make()
    assert breaker.state is State.CLOSED
    assert breaker.allow()


def test_opens_after_the_threshold_of_consecutive_failures():
    breaker, _ = make(threshold=3)
    fail(breaker, 2)
    assert breaker.state is State.CLOSED
    fail(breaker, 1)
    assert breaker.state is State.OPEN
    assert not breaker.allow()


def test_a_success_resets_the_failure_count():
    breaker, _ = make(threshold=3)
    fail(breaker, 2)
    assert breaker.allow()
    breaker.record_success()
    fail(breaker, 2)
    assert breaker.state is State.CLOSED


def test_open_rejects_until_the_recovery_timeout():
    breaker, clock = make(threshold=1, recovery=30.0)
    fail(breaker, 1)
    clock.now += 29
    assert not breaker.allow()
    assert breaker.state is State.OPEN
    clock.now += 1
    assert breaker.allow()
    assert breaker.state is State.HALF_OPEN


def test_half_open_allows_only_one_probe():
    breaker, clock = make(threshold=1, recovery=10)
    fail(breaker, 1)
    clock.now += 10
    assert breaker.allow()  # the probe
    assert not breaker.allow()  # everyone else keeps waiting


def test_probe_success_closes_the_circuit():
    breaker, clock = make(threshold=1, recovery=10)
    fail(breaker, 1)
    clock.now += 10
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state is State.CLOSED
    assert breaker.failures == 0
    assert breaker.allow()
    assert breaker.allow()


def test_probe_failure_reopens_and_restarts_the_timer():
    breaker, clock = make(threshold=1, recovery=10)
    fail(breaker, 1)
    clock.now += 10
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state is State.OPEN
    clock.now += 9
    assert not breaker.allow()  # the timer restarted when the probe failed
    clock.now += 1
    assert breaker.allow()


def test_retry_in_counts_down():
    breaker, clock = make(threshold=1, recovery=30)
    assert breaker.retry_in() == 0.0
    fail(breaker, 1)
    assert breaker.retry_in() == 30.0
    clock.now += 12
    assert breaker.retry_in() == 18.0


def test_an_ignored_outcome_frees_the_probe_slot():
    breaker, clock = make(threshold=1, recovery=10)
    fail(breaker, 1)
    clock.now += 10
    assert breaker.allow()
    assert not breaker.allow()
    breaker.record_ignored()
    assert breaker.allow()
    assert breaker.state is State.HALF_OPEN


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(recovery_timeout_s=-1)
