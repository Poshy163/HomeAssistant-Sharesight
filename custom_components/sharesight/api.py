"""Typed request layer over the ``SharesightAPI`` client.

Why this exists
---------------
``SharesightAPI.get_api_request`` is constructed with ``raise_for_status``
defaulting to False, in which case it returns the API's error body verbatim on
a failure.  Sharesight's JSON error envelope is::

    {"error": 2004, "reason": "...", "transaction_id": 12345}

- there is **no HTTP status anywhere in it**.  The integration used to try to
recover the status with ``response.get("status_code") or response.get("status")``,
which is only populated on the one path where the body was not parseable JSON,
so every status-gated branch (rate-limit backoff, brute-force lockout, the 404
"portfolio is gone" reconfigure prompt, the 401/403 reauth trigger) was
effectively dead, and no log line could ever say ``status=429``.

Constructing the client with ``raise_for_status=True`` instead makes it raise
``SharesightAuthError`` / ``SharesightRateLimitError`` / ``SharesightAPIError``,
which *do* carry the status.  This module normalises those - plus timeouts,
connection errors, and the 200-with-an-error-body case - into a single
``SharesightApiError`` that every caller can interrogate, and gives every log
line a consistent ``endpoint=v3/portfolios/1 [report], status=429, code=...``
suffix.

SharesightAPI exposes response metadata without mutable client-wide state.
That lets every coordinator feed the same app-scoped request gate with live
minute-budget headers even while requests are concurrent.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
from typing import Any

import aiohttp

from .const import (
    SHARESIGHT_HEAVY_CONCURRENCY,
    SHARESIGHT_MAX_REQUESTS_PER_MINUTE,
    SHARESIGHT_REQUESTS_PER_MINUTE_TARGET,
)

try:  # pragma: no cover - exercised implicitly by every request
    from SharesightAPI.exceptions import (
        SharesightAPIError,
        SharesightAuthError,
        SharesightError,
        SharesightRateLimitError,
    )
except ImportError:  # pragma: no cover - older library without typed errors

    class SharesightError(Exception):  # type: ignore[no-redef]
        """Fallback base when the installed library predates typed errors."""

    class SharesightAuthError(SharesightError):  # type: ignore[no-redef]
        """Fallback 401."""

    class SharesightAPIError(SharesightError):  # type: ignore[no-redef]
        """Fallback non-success status."""

        status_code: int | None = None
        response_data: Any = None

    class SharesightRateLimitError(SharesightAPIError):  # type: ignore[no-redef]
        """Fallback 429."""

        retry_after: float | None = None


# Sharesight's message for *any* 401, not only a genuine brute-force lockout:
# an expired access token produces the identical body.  The distinction matters
# because a real lockout means "stop calling for ten minutes" while an expired
# token just means "refresh and retry", so the coordinator only escalates to a
# lockout after the token has already been refreshed.
_LOCKOUT_MARKER = "locked out"

# Returned when too many calculation-heavy reports run at once.  Sharesight
# answers 403 with this text rather than a 429.
_PARALLEL_MARKERS = ("parallel", "too many requests", "rate limit")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One Sharesight request, and where its response belongs.

    ``key`` is the ``coordinator.data`` key the response is filed under.  None
    means "merge the response's own top-level keys", which is how the V3
    endpoints that already namespace themselves (``portfolios``, ``report``,
    ``benchmark``) are handled.
    """

    version: str
    path: str
    params: dict[str, Any] | None = None
    key: str | None = None
    #: Heavy endpoints count against Sharesight's 3-concurrent report limit.
    heavy: bool = field(default=False)
    #: Public fallback used only for version/capability mismatches.
    fallback_version: str | None = field(default=None)
    #: Poll cadence for slow-moving optional metadata (1 means every poll).
    refresh_every: int = field(default=1)

    def __str__(self) -> str:
        """``v3/portfolios/1/performance [report]`` - the log identity."""
        label = f"{self.version}/{self.path}"
        return f"{label} [{self.key}]" if self.key else label

    @property
    def cooldown_key(self) -> str:
        """Backoff identity.

        Includes ``key`` because the same path is polled with different
        parameters for different windows (past vs upcoming payouts, the five
        period performance windows) and each must back off independently.
        """
        return f"{self.version}/{self.path}#{self.key or ''}"

    def as_request(self) -> list[Any]:
        """The positional list the ``SharesightAPI`` client expects."""
        return [self.version, self.path, self.params, False]


#: Path fragments Sharesight limits to three concurrent requests.
_HEAVY_MARKERS = ("/performance", "/diversity", "/valuation", "/benchmark")


def is_heavy_path(path: str) -> bool:
    """Whether ``path`` is one of Sharesight's calculation-heavy reports."""
    return any(marker in path for marker in _HEAVY_MARKERS)


class SharesightApiError(Exception):
    """A normalised Sharesight failure, always carrying what we know.

    Every attribute is optional because different failure modes reveal
    different things: a connection reset has no status, a JSON error envelope
    has no status but does have Sharesight's numeric ``code``, and a typed
    library exception has the status but may have neither.
    """

    def __init__(
        self,
        endpoint: Endpoint | None = None,
        *,
        status: int | None = None,
        code: Any = None,
        reason: str | None = None,
        retry_after: float | None = None,
        transaction_id: Any = None,
        transport: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.status = status
        self.code = code
        self.reason = reason
        self.retry_after = retry_after
        self.transaction_id = transaction_id
        #: True for network-level failures (timeout, reset, DNS), which are
        #: always transient, versus API-level rejections which may not be.
        self.transport = transport
        self.headers = dict(headers or {})
        super().__init__(self.detail)

    @property
    def detail(self) -> str:
        """``status=403, code=2004, reason=..., txn=...`` for logs."""
        bits: list[str] = []
        if self.endpoint is not None:
            bits.append(f"endpoint={self.endpoint}")
        if self.status is not None:
            bits.append(f"status={self.status}")
        if self.code is not None:
            bits.append(f"code={self.code}")
        if self.reason:
            bits.append(f"reason={self.reason}")
        if self.transaction_id is not None:
            bits.append(f"txn={self.transaction_id}")
        return ", ".join(bits) or "unknown error"

    # -- classification ------------------------------------------------
    @property
    def is_unauthorised(self) -> bool:
        """401 - the token was rejected (expired, revoked, or locked out)."""
        return self.status == 401

    @property
    def is_forbidden(self) -> bool:
        """403 - the token is valid but not entitled to this endpoint."""
        return self.status == 403

    @property
    def is_not_found(self) -> bool:
        """404 - the portfolio was deleted or access to it was withdrawn."""
        return self.status == 404

    @property
    def is_lockout(self) -> bool:
        """Sharesight's ten-minute brute-force lockout.

        Textual because Sharesight reuses the plain 401 for it; the caller
        must have already ruled out a merely-expired token.
        """
        return self.is_unauthorised and _LOCKOUT_MARKER in (self.reason or "").lower()

    @property
    def is_rate_limited(self) -> bool:
        """429, or the 403 Sharesight uses for the 3-concurrent report cap."""
        if self.status == 429:
            return True
        reason = (self.reason or "").lower()
        if not self.is_forbidden:
            return False
        if any(marker in reason for marker in _PARALLEL_MARKERS):
            return True
        # Sharesight can signal an exhausted minute budget only through the
        # response headers while returning a generic HTTP 403 body. Preserve
        # the upstream client's classification instead of mistaking that for
        # a permanent plan/scope rejection.
        for key, value in self.headers.items():
            if str(key).lower() != "x-minuterate-remaining":
                continue
            try:
                return int(value) <= 0
            except TypeError, ValueError:
                return False
        return False

    @property
    def is_retryable(self) -> bool:
        """Whether trying the same request again could plausibly work."""
        if self.transport:
            return True
        if self.status is None:
            return False
        return self.status in (408, 425, 429, 500, 502, 503, 504)


def _envelope(payload: Any) -> tuple[Any, str | None, Any]:
    """``(code, reason, transaction_id)`` from a Sharesight error envelope."""
    if not isinstance(payload, dict):
        return None, str(payload)[:200] if payload else None, None
    reason = payload.get("reason") or payload.get("Reason") or payload.get("message")
    code = payload.get("error")
    # When the body was not JSON the client synthesises {"error": <text>}, so
    # a string "code" is really the reason.
    if isinstance(code, str) and reason is None:
        reason, code = code, None
    return code, str(reason) if reason is not None else None, payload.get("transaction_id")


def error_from_payload(endpoint: Endpoint, payload: Any) -> SharesightApiError:
    """Build an error from a 200 response whose body is an error envelope."""
    code, reason, txn = _envelope(payload)
    status = None
    if isinstance(payload, dict):
        raw_status = payload.get("status_code") or payload.get("status")
        try:
            status = int(raw_status) if raw_status is not None else None
        except TypeError, ValueError:
            status = None
    return SharesightApiError(endpoint, status=status, code=code, reason=reason, transaction_id=txn)


@dataclass(frozen=True, slots=True)
class SharesightApiResult:
    """Successful response body plus its concurrency-safe HTTP metadata."""

    data: Any
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SharesightRequestGate:
    """Consumer-app-scoped concurrency, minute budget, and cooldown state."""

    request_semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(8))
    heavy_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(SHARESIGHT_HEAVY_CONCURRENCY)
    )
    request_times: deque[float] = field(default_factory=deque)
    lockout_until: float = 0.0
    lockout_reason: str | None = None
    minute_limit: int = SHARESIGHT_MAX_REQUESTS_PER_MINUTE
    minute_remaining: int | None = None
    headers_observed_at: float | None = None

    # Keep a little headroom for config-flow probes, user-triggered services,
    # and other processes using the same OAuth consumer application.  The
    # local 330/minute window is the primary guard; this only reacts when the
    # server tells us the shared application budget is nearly exhausted.
    _server_budget_margin: int = 5

    def reserve(self) -> float | None:
        """Record one outgoing call, or return seconds until budget is free."""
        now = time.monotonic()
        cutoff = now - 60.0
        while self.request_times and self.request_times[0] <= cutoff:
            self.request_times.popleft()

        if len(self.request_times) >= SHARESIGHT_REQUESTS_PER_MINUTE_TARGET:
            return max(0.1, 60.0 - (now - self.request_times[0]))

        if (
            self.headers_observed_at is not None
            and now - self.headers_observed_at < 60.0
            and self.minute_remaining is not None
        ):
            if self.minute_remaining <= self._server_budget_margin:
                return max(0.1, 60.0 - (now - self.headers_observed_at))
            # Several requests can leave the semaphore before their responses
            # update the headers. Reserve against the last observed value so
            # that burst cannot knowingly overrun the server's remainder.
            self.minute_remaining -= 1
        self.request_times.append(now)
        return None

    def observe_headers(self, headers: dict[str, str]) -> None:
        """Capture Sharesight's latest minute budget, case-insensitively."""
        normalised = {str(key).lower(): str(value) for key, value in headers.items()}
        limit_raw = normalised.get("x-minuterate-limit")
        remaining_raw = normalised.get("x-minuterate-remaining")

        # Some successful Sharesight responses carry ``0 / 0`` as a
        # non-enforcing placeholder.  The response is still HTTP 200 and the
        # next requests continue to return complete payloads, so treating that
        # pair as an exhausted zero-request budget permanently starves every
        # slow and optional endpoint.  A real exhausted budget retains a
        # positive limit (normally 360) with zero remaining.
        # Apply a complete pair atomically.  Partial or malformed metadata must
        # not combine with an older response and manufacture a false budget.
        if limit_raw is None or remaining_raw is None:
            return
        try:
            observed_limit = int(limit_raw)
            observed_remaining = int(remaining_raw)
        except ValueError:
            return

        if observed_limit == 0 and observed_remaining == 0:
            # Clear any earlier server remainder as well as ignoring this
            # placeholder.  The local rolling 330/minute guard remains active.
            self.minute_limit = SHARESIGHT_MAX_REQUESTS_PER_MINUTE
            self.minute_remaining = None
            self.headers_observed_at = None
            return

        if observed_limit <= 0 or observed_remaining < 0 or observed_remaining > observed_limit:
            return

        self.minute_limit = observed_limit
        self.minute_remaining = observed_remaining
        self.headers_observed_at = time.monotonic()


async def async_request(
    client: Any, endpoint: Endpoint, access_token: str, timeout: float
) -> SharesightApiResult:
    """Perform one request, raising :class:`SharesightApiError` on any failure.

    Returns the parsed body with immutable response metadata. A successful
    body is normally a dict; the portfolio value series can answer with a bare
    list, which is passed through unchanged for the caller to normalise.
    """
    try:
        async with asyncio.timeout(timeout):
            if hasattr(client, "get_api_response"):
                rich_response = await client.get_api_response(endpoint.as_request(), access_token)
                response = SharesightApiResult(
                    data=rich_response.data,
                    status=int(rich_response.status),
                    headers=dict(rich_response.headers),
                )
            else:
                response = SharesightApiResult(
                    data=await client.get_api_request(endpoint.as_request(), access_token)
                )
    except TimeoutError as err:
        raise SharesightApiError(
            endpoint, reason=f"timed out after {timeout:g}s", transport=True
        ) from err
    except SharesightRateLimitError as err:
        payload = getattr(err, "response_data", None)
        code, reason, txn = _envelope(payload)
        raise SharesightApiError(
            endpoint,
            status=getattr(err, "status_code", 429) or 429,
            code=code,
            reason=reason or str(err),
            retry_after=getattr(err, "retry_after", None),
            transaction_id=txn,
            headers=dict(getattr(err, "response_headers", {}) or {}),
        ) from err
    except SharesightAuthError as err:
        payload = getattr(err, "response_data", None)
        code, reason, txn = _envelope(payload)
        raise SharesightApiError(
            endpoint,
            status=getattr(err, "status_code", 401) or 401,
            code=code,
            reason=reason or str(err),
            transaction_id=txn,
            headers=dict(getattr(err, "response_headers", {}) or {}),
        ) from err
    except SharesightAPIError as err:
        code, reason, txn = _envelope(getattr(err, "response_data", None))
        raise SharesightApiError(
            endpoint,
            status=getattr(err, "status_code", None),
            code=code,
            reason=reason or str(err),
            transaction_id=txn,
            headers=dict(getattr(err, "response_headers", {}) or {}),
        ) from err
    except SharesightError as err:  # pragma: no cover - defensive
        raise SharesightApiError(endpoint, reason=str(err)) from err
    except (aiohttp.ClientError, OSError) as err:
        raise SharesightApiError(
            endpoint,
            reason=f"{type(err).__name__}: {err}",
            transport=True,
        ) from err

    payload = response.data
    if payload is None:
        raise SharesightApiError(endpoint, reason="empty response body")
    if isinstance(payload, dict) and "error" in payload:
        # A 200 carrying an error envelope, or an older library returning the
        # body instead of raising.
        raise error_from_payload(endpoint, payload)
    if not isinstance(payload, (dict, list)):
        raise SharesightApiError(
            endpoint, reason=f"unexpected response type {type(payload).__name__}"
        )
    return response
