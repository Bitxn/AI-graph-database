"""Deterministic probe core.

Runs each configured check with httpx, records status/latency/assertion results,
and builds a Report. This layer needs no API key and makes no call other than to
the endpoints under test — it is fully deterministic and is what decides the
process exit code. The AI layer (diagnose.py) is layered on top and can never
change a verdict here.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import httpx

from oneport_apiwatch.config import BodyAssertion, Check, Config
from oneport_apiwatch.result import AssertionResult, CheckResult, Report

# Response bodies larger than this are truncated before storing/diagnosing.
_BODY_SNIPPET_LIMIT = 2000

_SENTINEL = object()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dig(data: Any, path: str) -> Any:
    """Traverse a dot-path (e.g. "data.items.0.id") into parsed JSON.

    Integer segments index into lists. Returns the _SENTINEL sentinel when the
    path does not exist, so assertions can distinguish "missing" from "null".
    """
    current = data
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return _SENTINEL
            current = current[segment]
        elif isinstance(current, list):
            try:
                idx = int(segment)
            except ValueError:
                return _SENTINEL
            if not -len(current) <= idx < len(current):
                return _SENTINEL
            current = current[idx]
        else:
            return _SENTINEL
    return current


def _evaluate_assertion(assertion: BodyAssertion, body: Any) -> AssertionResult:
    op = assertion.op
    actual = _dig(body, assertion.path)
    missing = actual is _SENTINEL
    shown = None if missing else actual

    if op == "exists":
        ok = (not missing) if assertion.exists else missing
        expected = f"exists={assertion.exists}"
    elif op == "equals":
        ok = (not missing) and actual == assertion.equals
        expected = assertion.equals
    elif op == "not_equals":
        ok = missing or actual != assertion.not_equals
        expected = f"!= {assertion.not_equals}"
    elif op == "contains":
        ok = (not missing) and _contains(actual, assertion.contains)
        expected = f"contains {assertion.contains}"
    elif op == "gt":
        ok = (not missing) and _is_number(actual) and actual > assertion.gt
        expected = f"> {assertion.gt}"
    elif op == "lt":
        ok = (not missing) and _is_number(actual) and actual < assertion.lt
        expected = f"< {assertion.lt}"
    else:  # pragma: no cover - guarded by config validation
        ok = False
        expected = op

    return AssertionResult(
        path=assertion.path,
        op=op,
        expected=expected,
        actual="<missing>" if missing else shown,
        ok=ok,
    )


def _contains(haystack: Any, needle: Any) -> bool:
    try:
        return needle in haystack
    except TypeError:
        return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _probe_once(check: Check, client: httpx.Client) -> CheckResult:
    """Probe one endpoint ONCE and return its deterministic result."""
    failures: list[str] = []
    assertions: list[AssertionResult] = []
    status_code: int | None = None
    latency_ms = 0.0
    body_snippet = ""
    transport_error = ""

    try:
        response = client.request(
            check.method,
            check.url,
            headers=check.resolved_headers(),
            timeout=check.timeout,
        )
        # httpx measures the round trip precisely, including connect + read.
        latency_ms = response.elapsed.total_seconds() * 1000
        status_code = response.status_code
        body_text = response.text
        body_snippet = body_text[:_BODY_SNIPPET_LIMIT]

        # 1. Status
        if status_code not in check.expected_statuses:
            expected = ", ".join(map(str, check.expected_statuses))
            failures.append(f"status {status_code} (expected {expected})")

        # 2. Latency budget
        if latency_ms > check.latency_budget_ms:
            failures.append(
                f"latency {latency_ms:.0f}ms over budget {check.latency_budget_ms}ms"
            )

        # 3. JSON body assertions
        if check.body:
            parsed, parse_err = _safe_json(response)
            if parse_err:
                failures.append(f"response body is not valid JSON: {parse_err}")
            else:
                for assertion in check.body:
                    result = _evaluate_assertion(assertion, parsed)
                    assertions.append(result)
                    if not result.ok:
                        failures.append(
                            f"assertion failed: {assertion.path} {result.expected} "
                            f"(actual: {result.actual})"
                        )
    except httpx.TimeoutException:
        transport_error = f"request timed out after {check.timeout}s"
        failures.append(transport_error)
    except httpx.HTTPError as exc:
        transport_error = f"{type(exc).__name__}: {exc}"
        failures.append(f"connection failed: {transport_error}")

    return CheckResult(
        name=check.name,
        url=check.url,
        method=check.method,
        ok=not failures,
        status_code=status_code,
        latency_ms=round(latency_ms, 1),
        expected_statuses=check.expected_statuses,
        latency_budget_ms=check.latency_budget_ms,
        error=transport_error,
        failures=failures,
        assertions=assertions,
        body_snippet=body_snippet,
    )


def _safe_json(response: httpx.Response) -> tuple[Any, str]:
    try:
        return response.json(), ""
    except Exception as exc:  # any decode failure is a reportable body failure
        return None, str(exc)[:120]


def _is_retryable(result: CheckResult) -> bool:
    """Only TRANSIENT failures are retried: a transport error (timeout, DNS,
    connection refused) or a 5xx. A 4xx, a latency-budget overrun, or a failed
    body assertion is deterministic — retrying wastes time and hides nothing."""
    if result.error:
        return True
    return result.status_code is not None and result.status_code >= 500


def probe_check(check: Check, client: httpx.Client | None = None) -> CheckResult:
    """Probe one endpoint, retrying transient failures per the check's config.

    Returns the first passing/deterministic result, or the last attempt. The
    number of attempts made is recorded on the result.
    """
    owns_client = client is None
    client = client or httpx.Client(follow_redirects=True)
    max_attempts = 1 + max(0, check.retries)
    try:
        result = _probe_once(check, client)
        made = 1
        while made < max_attempts and not result.ok and _is_retryable(result):
            time.sleep(max(0, check.retry_backoff_ms) / 1000)
            result = _probe_once(check, client)
            made += 1
        result.attempts = made
        return result
    finally:
        if owns_client:
            client.close()


def run_checks(config: Config) -> Report:
    """Probe every check, concurrently (up to config.concurrency), sharing one
    thread-safe HTTP client. Results are returned in config order."""
    checks = config.checks
    results: list[CheckResult | None] = [None] * len(checks)
    workers = max(1, min(config.concurrency, len(checks)))

    with httpx.Client(follow_redirects=True) as client:
        if workers == 1:
            for i, check in enumerate(checks):
                results[i] = probe_check(check, client=client)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(probe_check, c, client): i for i, c in enumerate(checks)}
                for fut in as_completed(futures):
                    results[futures[fut]] = fut.result()

    return Report(checks=[r for r in results if r is not None], generated_at=_now_iso())
