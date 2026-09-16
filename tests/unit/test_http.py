import logging
from collections.abc import Callable

import httpx
import pytest

from stackoverflow_vitality.ingestion.http import (
    HttpxStackExchangeClient,
    InvalidApiResponse,
    NonRecoverableHttpError,
    RetryExhausted,
)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_attempts: int = 4,
    sleeps: list[float] | None = None,
    jitter: Callable[[float], float] = lambda base: 0,
) -> HttpxStackExchangeClient:
    return HttpxStackExchangeClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_attempts=max_attempts,
        initial_backoff_seconds=1,
        max_backoff_seconds=10,
        sleeper=(sleeps.append if sleeps is not None else lambda seconds: None),
        jitter=jitter,
    )


def test_builds_expected_request_and_captures_quota() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["user_agent"] = request.headers["user-agent"]
        return httpx.Response(
            200,
            json={"items": [], "has_more": False, "quota_max": 10000, "quota_remaining": 9999},
        )

    client = make_client(handler)
    response = client.get(
        "https://api.stackexchange.com/2.3/questions",
        {
            "site": "stackoverflow",
            "tagged": "python",
            "fromdate": 1,
            "todate": 2,
            "page": 1,
            "pagesize": 100,
        },
    )
    assert "tagged=python" in str(captured["url"])
    assert "fromdate=1" in str(captured["url"])
    assert "page=1" in str(captured["url"])
    assert captured["user_agent"] == "stackoverflow-vitality-observatory/0.1"
    assert response.quota_max == 10000
    assert response.quota_remaining == 9999
    client.close()


@pytest.mark.parametrize("status", [408, 429, 502, 503, 504])
def test_transient_statuses_retry_until_success(status: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, json={"backoff": 2})
        return httpx.Response(200, json={"items": [], "has_more": False})

    sleeps: list[float] = []
    client = make_client(handler, sleeps=sleeps)
    response = client.get("https://api.stackexchange.com/2.3/questions", {})
    assert response.attempts == 2
    assert sleeps == [2]
    client.close()


def test_timeout_retries_and_exhaustion_is_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    sleeps: list[float] = []
    client = make_client(handler, max_attempts=3, sleeps=sleeps)
    with pytest.raises(RetryExhausted):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert len(sleeps) == 2
    client.close()


def test_exponential_backoff_and_injected_jitter_are_deterministic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    sleeps: list[float] = []
    client = make_client(handler, max_attempts=3, sleeps=sleeps, jitter=lambda base: 0.25)
    with pytest.raises(RetryExhausted):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert sleeps == [1.25, 2.25]
    client.close()


def test_bad_request_does_not_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error_id": 400})

    client = make_client(handler)
    with pytest.raises(NonRecoverableHttpError):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert calls == 1
    client.close()


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="not-json"), httpx.Response(200, json={"has_more": False})],
)
def test_invalid_json_or_missing_items_is_rejected(response: httpx.Response) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(InvalidApiResponse):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    client.close()


def test_api_key_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "super-secret-key"
    client = HttpxStackExchangeClient(
        api_key=secret,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"items": [], "has_more": False})
            )
        ),
    )
    with caplog.at_level(logging.INFO):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert secret not in caplog.text
    client.close()


def test_api_mandated_backoff_is_not_capped_and_local_delay_is_capped(caplog):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"backoff": 70})
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"items": [], "has_more": False})

    sleeps = []
    client = make_client(handler, sleeps=sleeps, jitter=lambda base: 1000)
    with caplog.at_level(logging.INFO):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert sleeps == [70, 10]
    assert '"delay_seconds": 70.0' in caplog.text
    client.close()


def test_last_failed_attempt_obeys_mandatory_wait_before_return():
    sleeps = []
    client = make_client(
        lambda request: httpx.Response(429, json={"backoff": 70}),
        max_attempts=1,
        sleeps=sleeps,
    )
    with pytest.raises(RetryExhausted):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert sleeps == [70]
    client.close()


def test_retry_exception_never_contains_transport_secret():
    def handler(request):
        raise httpx.ConnectError("https://example.test?key=private-secret", request=request)

    client = make_client(handler, max_attempts=1)
    with pytest.raises(RetryExhausted) as error:
        client.get("https://api.stackexchange.com/2.3/questions", {})
    assert "private-secret" not in str(error.value)
    client.close()


@pytest.mark.parametrize(
    "payload", [{"items": []}, {"items": [], "has_more": False, "quota_remaining": True}]
)
def test_missing_has_more_and_boolean_quota_rejected(payload):
    client = make_client(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(InvalidApiResponse):
        client.get("https://api.stackexchange.com/2.3/questions", {})
    client.close()


def test_endpoint_containing_secret_is_rejected_before_transport():
    def forbidden(request):
        raise AssertionError("transport must not be called")

    client = make_client(forbidden)
    with pytest.raises(ValueError):
        client.get("https://example.test/questions?key=private-secret", {})
    client.close()
