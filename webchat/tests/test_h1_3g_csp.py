"""H1.3G: a `srcdoc` document (the Revenue MCP App View, mounted by
webchat/host-src/src/host.mjs's `iframe.srcdoc = templateHtml`) inherits its
OWNER document's Content-Security-Policy in addition to its own <meta> CSP
(CSP3) - both are enforced independently. webchat's own outer "/" page CSP
(_set_security_headers in server.py) previously granted only `script-src
'self'; style-src 'self'`, which silently blocked the View's inline
script/style regardless of what its own <meta> CSP said, because CSP
enforcement is conjunctive across every policy that applies. These tests
cover the fix: mcp_app.compute_inline_csp_hashes (the same sha256-over-UTF8
-bytes algorithm mcp/apps/revenue/build.mjs uses to bake the hash into the
View's own <meta> tag) and server.py's use of it to allowlist the exact
same inline blocks on the outer page's own CSP header - never
'unsafe-inline'.
"""

import hashlib
import base64
import re

import pytest

import mcp_app
import server


def _declared_hashes_in(html: str) -> tuple[str, str]:
    """Reads back the sha256- hashes already baked into the View's own
    <meta> CSP tag (by mcp/apps/revenue/build.mjs) - the ground truth for
    what dist/view.html currently declares, independent of any value
    hardcoded in a test."""
    script_match = re.search(r"script-src '(sha256-[A-Za-z0-9+/]+=*)'", html)
    style_match = re.search(r"style-src '(sha256-[A-Za-z0-9+/]+=*)'", html)
    assert script_match and style_match, "expected both a script-src and style-src sha256 hash in the CSP <meta> tag"
    return script_match.group(1), style_match.group(1)


FIXTURE_HTML = """<!doctype html>
<html>
<head>
<style>
  body { color: red; }
</style>
</head>
<body>
<script>
  console.log("hello");
</script>
</body>
</html>
"""


def _expected_hash(source_text: str) -> str:
    digest = hashlib.sha256(source_text.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def test_compute_inline_csp_hashes_matches_manual_computation_for_a_fixture():
    script_hash, style_hash = mcp_app.compute_inline_csp_hashes(FIXTURE_HTML)

    assert script_hash == _expected_hash('\n  console.log("hello");\n')
    assert style_hash == _expected_hash("\n  body { color: red; }\n")


def test_compute_inline_csp_hashes_matches_the_real_committed_view_html():
    """Proves this function's algorithm agrees, byte-for-byte, with
    mcp/apps/revenue/build.mjs's own hash computation on the REAL committed
    artifact - independent verification, not a hardcoded value, so it can
    never drift when the View's content legitimately changes (e.g. H1.3H's
    percentage-formatting fix changed the bundled JS and this hash with
    it). The original H1.3G incident's exact Edge-reported values are
    covered by test_h1_3g_csp.py's git history, not pinned here.
    """
    from pathlib import Path

    view_html_path = Path(__file__).resolve().parents[2] / "mcp" / "apps" / "revenue" / "dist" / "view.html"
    if not view_html_path.exists():
        pytest.skip("mcp/apps/revenue/dist/view.html not built in this checkout")
    html = view_html_path.read_text(encoding="utf-8")

    script_hash, style_hash = mcp_app.compute_inline_csp_hashes(html)

    # H1.3H: the script hash changed when format.ts's percentage fix
    # changed the bundled JS - this asserts the CURRENT committed
    # dist/view.html's own declared hash (proven self-consistent by
    # mcp/apps/revenue/test/csp.test.mjs) rather than re-deriving it here,
    # so this test can't silently drift from what's actually shipped.
    declared_script_hash, declared_style_hash = _declared_hashes_in(html)
    assert script_hash == declared_script_hash
    assert style_hash == declared_style_hash


def test_compute_inline_csp_hashes_returns_none_when_no_style_or_script_present():
    script_hash, style_hash = mcp_app.compute_inline_csp_hashes("<html><body>no inline blocks here</body></html>")
    assert script_hash is None
    assert style_hash is None


def test_get_current_view_csp_hashes_fails_closed_to_none_when_mcp_is_unreachable():
    def _raising_call_async(coro):
        coro.close()
        raise ConnectionError("simulated MCP unreachable")

    script_hash, style_hash = mcp_app.get_current_view_csp_hashes(_raising_call_async, "http://localhost:1/does-not-exist", None)
    assert script_hash is None
    assert style_hash is None


@pytest.fixture(autouse=True)
def _clean_server_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "STORE_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(server, "SCOPE_PRIVATE_KEY", "dummy-not-a-real-key-value")
    yield


def test_index_csp_includes_the_exact_hashes_when_the_view_template_is_available(monkeypatch):
    monkeypatch.setattr(mcp_app, "get_current_view_csp_hashes", lambda *a, **k: ("sha256-AAAA", "sha256-BBBB"))

    resp = server.app.test_client().get("/")
    csp = resp.headers["Content-Security-Policy"]

    assert "script-src 'self' 'sha256-AAAA'" in csp
    assert "style-src 'self' 'sha256-BBBB'" in csp
    assert "unsafe-inline" not in csp


def test_index_csp_falls_back_to_plain_self_when_the_view_template_is_unavailable(monkeypatch):
    monkeypatch.setattr(mcp_app, "get_current_view_csp_hashes", lambda *a, **k: (None, None))

    resp = server.app.test_client().get("/")
    csp = resp.headers["Content-Security-Policy"]

    assert "script-src 'self';" in csp
    assert "style-src 'self';" in csp
    assert "unsafe-inline" not in csp


def test_non_index_routes_never_carry_a_view_csp_hash(monkeypatch):
    """The hash lookup is scoped to `/` only (the one full-page navigation
    whose CSP a later `srcdoc` iframe actually inherits) - other routes
    must not pay for (or accidentally depend on) an MCP round-trip just to
    build a response header.
    """
    calls = []

    def _spy(*a, **k):
        calls.append(1)
        return ("sha256-AAAA", "sha256-BBBB")

    monkeypatch.setattr(mcp_app, "get_current_view_csp_hashes", _spy)

    server.app.test_client().get("/health/live")

    assert calls == []
