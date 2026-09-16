"""Concrete, retrying Stack Exchange API client."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import httpx

from stackoverflow_vitality.ingestion.interfaces import ApiResponse, HttpClient

LOGGER = logging.getLogger(__name__)


class StackExchangeClientError(Exception):
    """Base error for API client failures."""


class RecoverableHttpError(StackExchangeClientError):
    """A transport or HTTP failure eligible for retry."""

    def __init__(self, message: str, *, backoff_seconds: int | None = None) -> None:
        super().__init__(message)
        self.backoff_seconds = backoff_seconds


class NonRecoverableHttpError(StackExchangeClientError):
    """A permanent HTTP or API response failure."""


class RetryExhausted(RecoverableHttpError):
    """All configured attempts failed."""


class InvalidApiResponse(NonRecoverableHttpError):
    """The API response does not satisfy the minimum response contract."""


Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Jitter = Callable[[float], float]


def _default_jitter(base_delay: float) -> float:
    return random.uniform(0, base_delay)


class HttpxStackExchangeClient(HttpClient):
    """Synchronous API client with bounded retries and injectable timing."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.stackexchange.com/2.3",
        user_agent: str = "stackoverflow-vitality-observatory/0.1",
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_attempts: int = 4,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        jitter: Jitter = _default_jitter,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("base_url must use HTTPS")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if initial_backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("backoff values cannot be negative")
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.max_attempts = max_attempts
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.api_key = api_key
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    def get(self, endpoint: str, params: dict[str, str | int]) -> ApiResponse:
        url = (
            endpoint
            if endpoint.startswith("https://")
            else f"{self.base_url}/{endpoint.lstrip('/')}"
        )
        parsed_url = urlsplit(url)
        if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            raise ValueError("endpoint must not contain credentials, query or fragment")
        safe_params = dict(params)
        if self.api_key:
            safe_params["key"] = self.api_key
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            started = self._clock()
            try:
                response = self._client.get(
                    url,
                    params=safe_params,
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                )
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    backoff = self._response_backoff(response)
                    raise RecoverableHttpError(
                        f"HTTP {response.status_code}",
                        backoff_seconds=backoff,
                    )
                if response.status_code < 200 or response.status_code >= 300:
                    mandated = self._response_backoff(response)
                    if mandated:
                        self._sleeper(mandated)
                    raise NonRecoverableHttpError(f"HTTP {response.status_code}")
                payload = self._json_object(response)
                if "error_id" in payload:
                    mandated = _optional_nonnegative_int(payload, "backoff")
                    if mandated:
                        self._sleeper(mandated)
                parsed = self._parse_payload(payload)
                parsed = replace(
                    parsed,
                    attempts=attempt,
                    duration_ms=int((self._clock() - started) * 1000),
                )
                LOGGER.info(
                    json.dumps(
                        {
                            "event": "api_response",
                            "endpoint": url,
                            "page": safe_params.get("page"),
                            "attempt": attempt,
                            "items_received": len(parsed.items),
                            "quota_remaining": parsed.quota_remaining,
                            "has_more": parsed.has_more,
                            "backoff_seconds": parsed.backoff_seconds,
                            "duration_ms": parsed.duration_ms,
                            "status": "success",
                        },
                        sort_keys=True,
                    )
                )
                return parsed
            except RecoverableHttpError as error:
                last_error = error
            except httpx.TimeoutException as error:
                last_error = error
            except httpx.TransportError as error:
                last_error = error
            except (NonRecoverableHttpError, InvalidApiResponse):
                raise

            if attempt == self.max_attempts:
                mandated = getattr(last_error, "backoff_seconds", None)
                if mandated:
                    self._sleeper(mandated)
                break
            base_delay = min(
                self.max_backoff_seconds,
                self.initial_backoff_seconds * (2 ** (attempt - 1)),
            )
            api_backoff = getattr(last_error, "backoff_seconds", None)
            retry_delay = min(self.max_backoff_seconds, base_delay + self._jitter(base_delay))
            if api_backoff is not None:
                retry_delay = max(retry_delay, float(api_backoff))
            LOGGER.info(
                json.dumps(
                    {
                        "event": "api_retry",
                        "attempt": attempt,
                        "delay_seconds": retry_delay,
                        "backoff_seconds": api_backoff,
                        "error_type": type(last_error).__name__,
                    },
                    sort_keys=True,
                )
            )
            self._sleeper(retry_delay)

        raise RetryExhausted(f"request failed after {self.max_attempts} attempts") from None

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise InvalidApiResponse("response body is not valid JSON") from error
        if not isinstance(payload, dict):
            raise InvalidApiResponse("response body must be a JSON object")
        return payload

    @staticmethod
    def _response_backoff(response: httpx.Response) -> int | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            return _optional_nonnegative_int(payload, "backoff")
        return None

    @staticmethod
    def _parse_payload(payload: Mapping[str, Any]) -> ApiResponse:
        if "error_id" in payload:
            raise NonRecoverableHttpError(f"Stack Exchange API error {payload['error_id']}")
        items = payload.get("items")
        has_more = payload.get("has_more")
        if not isinstance(items, list) or not isinstance(has_more, bool):
            raise InvalidApiResponse("response must contain list items and boolean has_more")
        if any(not isinstance(item, dict) for item in items):
            raise InvalidApiResponse("items must contain JSON objects")
        return ApiResponse(
            items=items,
            has_more=has_more,
            quota_max=_optional_nonnegative_int(payload, "quota_max"),
            quota_remaining=_optional_nonnegative_int(payload, "quota_remaining"),
            backoff_seconds=_optional_nonnegative_int(payload, "backoff"),
            payload=dict(payload),
        )


def _optional_nonnegative_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise InvalidApiResponse(f"{key} must be a non-negative integer")
    return value
