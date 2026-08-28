"""F1.1: regression tests for webchat/scripts/create_agent_version.py's
corrected agent-version-creation call shape. Never calls live Foundry -
`AIProjectClient`/`DefaultAzureCredential` are always replaced with fakes
that raise on anything unexpected, so a call this script shouldn't make
(publish, activate, set a stable-version selector, delete/update an
existing version) fails the test loudly rather than silently succeeding
against a permissive stub.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import create_agent_version as cav  # noqa: E402


class _FakeVersion:
    def __init__(self, version: str, name: str, definition):
        self.version = version
        self.name = name
        self.definition = definition


class _FakeAgentsClient:
    """Exposes ONLY create_version - any other attribute access (a publish/
    activate/set-stable-version/update/delete call this script must never
    make) raises AttributeError, so such a call fails the test loudly
    instead of silently succeeding against a permissive stub."""

    def __init__(self, next_version: str = "13"):
        self.create_version_calls: list[dict] = []
        self._next_version = next_version

    def create_version(self, **kwargs):
        self.create_version_calls.append(kwargs)
        return _FakeVersion(self._next_version, kwargs["agent_name"], kwargs["definition"])


class _FakeProject:
    def __init__(self, agents_client: _FakeAgentsClient):
        self.agents = agents_client


def _install_fakes(monkeypatch, agents_client: _FakeAgentsClient):
    monkeypatch.setattr(cav, "AIProjectClient", lambda endpoint, credential: _FakeProject(agents_client))
    monkeypatch.setattr(cav, "DefaultAzureCredential", lambda: object())


def test_publish_flag_is_rejected_before_any_client_is_constructed(monkeypatch, capsys):
    def _explode(*a, **k):
        raise AssertionError("AIProjectClient must not be constructed when --publish is rejected")

    monkeypatch.setattr(cav, "AIProjectClient", _explode)
    monkeypatch.setattr(cav, "DefaultAzureCredential", _explode)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py", "--publish"])

    with pytest.raises(SystemExit) as exc_info:
        cav.main()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "no longer" in err.lower() or "not" in err.lower()
    assert "ARIEL_AGENT_VERSION" in err


def test_f1_revenue_call_never_passes_a_draft_kwarg(monkeypatch):
    agents = _FakeAgentsClient()
    _install_fakes(monkeypatch, agents)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py", "--f1-revenue"])

    cav.main()

    assert len(agents.create_version_calls) == 1
    call = agents.create_version_calls[0]
    assert "draft" not in call
    assert set(call.keys()) == {"agent_name", "definition", "description"}


def test_f1_revenue_uses_agent_name_keyword_and_configured_agent(monkeypatch):
    agents = _FakeAgentsClient()
    _install_fakes(monkeypatch, agents)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py", "--f1-revenue"])

    cav.main()

    assert agents.create_version_calls[0]["agent_name"] == cav.AGENT_NAME


def test_f1_revenue_definition_contains_only_the_two_revenue_tools(monkeypatch):
    agents = _FakeAgentsClient()
    _install_fakes(monkeypatch, agents)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py", "--f1-revenue"])

    cav.main()

    definition = agents.create_version_calls[0]["definition"]
    tool_names = {t["name"] for t in definition.tools}
    assert tool_names == {"get_performance_digest", "get_result_evidence"}


def test_default_dmr_path_uses_the_same_corrected_call_shape(monkeypatch):
    agents = _FakeAgentsClient()
    _install_fakes(monkeypatch, agents)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py"])

    cav.main()

    call = agents.create_version_calls[0]
    assert "draft" not in call
    assert set(call.keys()) == {"agent_name", "definition", "description"}
    tool_names = {t["name"] for t in call["definition"].tools}
    assert tool_names == {
        "get_dmr_revenue_trend", "get_dmr_revenue_snapshot", "get_dmr_segment_mix",
        "get_dmr_fnb_performance", "get_dmr_holdings_outlook",
    }


def test_no_publish_or_active_version_selector_api_is_ever_called(monkeypatch):
    """_FakeAgentsClient exposes ONLY create_version - any attempt to call
    a publish/activate/update/delete-style method raises AttributeError
    before this assertion, so reaching here already proves none was called;
    this test names that invariant explicitly."""
    agents = _FakeAgentsClient()
    _install_fakes(monkeypatch, agents)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py", "--f1-revenue"])

    cav.main()

    assert len(agents.create_version_calls) == 1  # exactly one call, nothing else


def test_returned_version_number_is_reported_without_leaking_instructions(monkeypatch, capsys):
    agents = _FakeAgentsClient(next_version="13")
    _install_fakes(monkeypatch, agents)
    monkeypatch.setattr(sys, "argv", ["create_agent_version.py", "--f1-revenue"])

    cav.main()

    out = capsys.readouterr().out
    assert "13" in out
    assert "get_performance_digest" in out
    # The full instructions text must never be dumped to stdout.
    assert cav.build_f1_instructions() not in out
