"""FabricQueryService hardening - retries, timeouts, response-size/schema
safety, and sanitized error mapping. Everything runs against a real local
HTTP server (see conftest.py's `scripted_server`), not a mocked transport
object, so the actual urllib3 Retry/backoff/streaming machinery is what's
under test, not a stand-in for it. Credential acquisition is monkeypatched
out (there's no live Azure AD to call from a unit test) - that's a real,
stated gap, not a claim this proves managed identity works end to end; see
the Phase 2 plan's Verification section.
"""

import json

import pytest

import fabric_client.service as service
from config import FabricAuthMode, FabricOptions
from conftest import fabric_response_body
from fabric_client.result import ErrorCode


def _make_options(**overrides):
    defaults = dict(
        auth_mode=FabricAuthMode.CLIENT_SECRET,
        tenant_id="t",
        client_id="c",
        client_secret="s",
        managed_identity_client_id=None,
        workspace_id="ws",
        dataset_id="ds",
        scope_public_keys={},
    )
    defaults.update(overrides)
    return FabricOptions(**defaults)


@pytest.fixture
def make_service(monkeypatch, scripted_server):
    def _make(responses):
        url, handler_cls = scripted_server(responses)
        monkeypatch.setattr(service, "_EXECUTE_QUERIES_URL_TEMPLATE", url)
        svc = service.FabricQueryService(_make_options())
        monkeypatch.setattr(svc, "_get_access_token", lambda: "test-token")
        return svc, handler_cls

    return _make


def test_success_on_first_attempt(make_service):
    svc, handler_cls = make_service([{"status": 200, "body": fabric_response_body([{"[Foo]": "bar"}])}])

    result = svc.run_query("EVALUATE {1}")

    assert result.error is None
    assert result.rows == [{"[Foo]": "bar"}]
    assert len(handler_cls.calls) == 1


def test_retries_503_twice_then_succeeds_within_budget(make_service):
    """total=2 means up to 3 attempts total - two failures then a success
    on the third attempt is exactly at that limit. This also proves POST is
    actually in the retry allowlist: urllib3's default allowlist excludes
    POST, so if that weren't set explicitly, the first 503 would propagate
    immediately and there would be exactly 1 call, not 3."""
    svc, handler_cls = make_service(
        [
            {"status": 503, "body": b"{}"},
            {"status": 503, "body": b"{}"},
            {"status": 200, "body": fabric_response_body([{"[Foo]": "bar"}])},
        ]
    )

    result = svc.run_query("EVALUATE {1}")

    assert result.error is None
    assert len(handler_cls.calls) == 3


def test_exhausts_retry_budget_and_fails_closed(make_service):
    svc, handler_cls = make_service([{"status": 503, "body": b"{}"}])

    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.FABRIC_UNAVAILABLE
    assert result.error.retryable is True
    assert len(handler_cls.calls) == 3  # initial attempt + 2 retries, all exhausted


@pytest.mark.parametrize(
    "status,expected_code",
    [
        (400, ErrorCode.INTERNAL_ERROR),
        (401, ErrorCode.AUTHENTICATION_FAILED),
        (403, ErrorCode.PERMISSION_DENIED),
    ],
)
def test_non_retryable_statuses_are_attempted_exactly_once(make_service, status, expected_code):
    svc, handler_cls = make_service([{"status": status, "body": b"{}"}])

    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == expected_code
    assert result.error.retryable is False
    assert len(handler_cls.calls) == 1


def test_read_timeout_maps_to_fabric_timeout_and_is_not_retried(make_service, monkeypatch):
    """`read=0` in the retry config is deliberate - a read timeout can mean
    Fabric already started executing the query, so this should surface
    after exactly one attempt, not be retried blind."""
    monkeypatch.setattr(service, "_READ_TIMEOUT", 0.2)
    svc, handler_cls = make_service([{"status": 200, "body": fabric_response_body([]), "sleep": 1.0}])

    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.FABRIC_TIMEOUT
    assert result.error.retryable is True
    assert len(handler_cls.calls) == 1


def test_oversized_response_rejected_via_correct_content_length(make_service, monkeypatch):
    monkeypatch.setattr(service, "_MAX_RESPONSE_BYTES", 50)
    body = fabric_response_body([{"[Foo]": "x" * 200}])

    svc, handler_cls = make_service([{"status": 200, "body": body}])
    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.FABRIC_UNAVAILABLE


def test_oversized_chunked_response_rejected_without_content_length(make_service, monkeypatch):
    """No Content-Length header exists at all for a chunked response - the
    cap has to be enforced from the streamed byte count, not the header."""
    monkeypatch.setattr(service, "_MAX_RESPONSE_BYTES", 50)
    body = fabric_response_body([{"[Foo]": "x" * 200}])

    svc, handler_cls = make_service([{"status": 200, "body": body, "chunked": True}])
    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.FABRIC_UNAVAILABLE


def test_gzip_response_expanding_past_cap_is_rejected(make_service, monkeypatch):
    """A highly-compressible body has a small (honest) compressed
    Content-Length but decodes far past the cap - `iter_content` yields
    already-decoded bytes, so the streamed check still catches it."""
    monkeypatch.setattr(service, "_MAX_RESPONSE_BYTES", 50)
    body = fabric_response_body([{"[Foo]": "x" * 2000}])

    svc, handler_cls = make_service([{"status": 200, "body": body, "gzip": True}])
    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.FABRIC_UNAVAILABLE


def test_dishonest_low_content_length_yields_a_safe_schema_error(make_service, monkeypatch):
    """A server that declares a Content-Length shorter than what it sends
    isn't something a client can override - HTTP/1.1 framing means the
    client reads exactly the declared byte count and treats that as the
    whole message. The safe outcome here is that the truncated body fails
    to parse as JSON (RESPONSE_SCHEMA_CHANGED) - not that it gets accepted
    as if it were a complete, valid response."""
    body = fabric_response_body([{"[Foo]": "x" * 200}])

    svc, handler_cls = make_service([{"status": 200, "body": body, "false_content_length": 10}])
    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_malformed_json_is_rejected(make_service):
    svc, handler_cls = make_service([{"status": 200, "body": b"not json {"}])

    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_valid_json_with_wrong_shape_is_rejected(make_service):
    svc, handler_cls = make_service([{"status": 200, "body": json.dumps({"unexpected": True}).encode()}])

    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.RESPONSE_SCHEMA_CHANGED


def test_row_count_ceiling_is_enforced(make_service, monkeypatch):
    monkeypatch.setattr(service, "_MAX_ROWS", 3)
    rows = [{"[Foo]": i} for i in range(5)]

    svc, handler_cls = make_service([{"status": 200, "body": fabric_response_body(rows)}])
    result = svc.run_query("EVALUATE {1}")

    assert result.error is not None
    assert result.error.code == ErrorCode.INTERNAL_ERROR


def test_clamped_retry_caps_an_excessive_retry_after():
    retry = service._ClampedRetry(total=2)

    class FakeResponse:
        headers = {"Retry-After": "9999"}

    assert retry.get_retry_after(FakeResponse()) == service._MAX_RETRY_AFTER_SECONDS


def test_shared_session_carries_no_default_authorization_header():
    """The session is reused across concurrent invocations - a default
    Authorization header set on it would leak one call's token into every
    other call sharing the session. Headers must only ever be passed
    per-request."""
    assert "Authorization" not in service._SESSION.headers
