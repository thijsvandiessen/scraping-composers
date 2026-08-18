"""Pacing and daily-quota enforcement for Gemini requests, split out of
:mod:`.gemini_client` to keep that module under the repo's file-length cap."""

from __future__ import annotations

import time


class RequestGovernor:
    """Holds the two free-tier guards a sequential extraction run needs on top
    of :func:`composer_http.call_with_retries`'s retry-on-429 backoff: a floor on
    the gap between requests, and a hard stop once a daily cap is reached."""

    def __init__(self) -> None:
        self._min_interval_s = 0.0
        self._last_request_at: float | None = None
        self._max_requests_per_day = 0
        self._request_count = 0

    def with_pacing(self, min_interval_s: float) -> RequestGovernor:
        """Hold at least *min_interval_s* between successive requests. Mutates,
        and returns ``self`` so it can be chained onto the constructor call."""
        self._min_interval_s = min_interval_s
        return self

    def with_daily_limit(self, max_requests_per_day: int) -> RequestGovernor:
        """Refuse to send more than *max_requests_per_day* requests. Mutates, and
        returns ``self`` so it can be chained like :meth:`with_pacing`. 0 disables
        the check."""
        self._max_requests_per_day = max_requests_per_day
        return self

    def before_request(self) -> None:
        """Enforce the daily cap, then the pacing floor. Call once per attempt
        (including retries), immediately before the request goes out."""
        self._check_daily_limit()
        self._pace()

    def _pace(self) -> None:
        """Block until at least ``_min_interval_s`` has passed since the last
        request, so a sequential run stays under the per-minute quota instead of
        only backing off after it's already been breached."""
        if self._min_interval_s <= 0:
            return
        if self._last_request_at is not None:
            remaining = self._min_interval_s - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _check_daily_limit(self) -> None:
        """Raise once ``_max_requests_per_day`` requests have already gone out.
        The free tier's daily quota doesn't reopen until the next reset, so unlike
        the per-minute quota there's no backoff that can wait it out — stopping
        the run here is the only useful reaction. A plain ``RuntimeError``, not an
        ``httpx.HTTPError``, so ``call_with_retries`` lets it straight through
        instead of retrying it."""
        if self._max_requests_per_day <= 0:
            return
        if self._request_count >= self._max_requests_per_day:
            raise RuntimeError(
                f"gemini: reached the daily request limit ({self._max_requests_per_day}); "
                "stopping this run rather than retrying into a quota that won't reset until tomorrow"
            )
        self._request_count += 1
