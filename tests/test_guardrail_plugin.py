"""Tests for GuardrailPlugin org-argument binding."""

from unittest.mock import MagicMock

import pytest

from lightspeed_agent.api.a2a import guardrail_plugin
from lightspeed_agent.api.a2a.guardrail_plugin import GuardrailPlugin


class TestCollectOrgValues:
    """Unit tests for ``_collect_org_values`` (key names and nested structures)."""

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            pytest.param({"org_id": "one"}, ["one"], id="org_id_scalar"),
            pytest.param({"organization_id": "two"}, ["two"], id="organization_id"),
            pytest.param({"rh_org_id": "three"}, ["three"], id="rh_org_id"),
            pytest.param({"orgid": "four"}, ["four"], id="orgid_collapsed"),
            pytest.param({"organizationid": "five"}, ["five"], id="organizationid_collapsed"),
            pytest.param({"Org-Id": "six"}, ["six"], id="hyphen_normalized_to_org_id"),
            pytest.param({"ORGANIZATION_ID": "seven"}, ["seven"], id="uppercase_key"),
            pytest.param({"organizationId": "eight"}, ["eight"], id="camelCase_to_organizationid"),
            pytest.param(
                {"not_tracked": "x", "wrapper": {"org_id": "nine"}},
                ["nine"],
                id="nested_org_id_only",
            ),
            pytest.param({"org_ids": ["wont_collect"]}, [], id="org_ids_plural_not_in_allowlist"),
            pytest.param(
                {"query": {"rh_org_id": "ten"}},
                ["ten"],
                id="nested_rh_org_id",
            ),
        ],
    )
    def test_key_names_and_nesting(self, payload: dict, expected: list[str]) -> None:
        assert guardrail_plugin._collect_org_values(payload) == expected

    def test_list_of_strings_under_org_id(self) -> None:
        assert guardrail_plugin._collect_org_values(
            {"org_id": ["a", "b", "a"]}
        ) == ["a", "b", "a"]

    def test_tuple_of_ints_under_org_id(self) -> None:
        assert guardrail_plugin._collect_org_values({"org_id": (10, 20)}) == ["10", "20"]

    def test_list_of_dicts_under_non_org_key(self) -> None:
        """Rows are dicts with org_id; parent key is not org-related."""
        assert guardrail_plugin._collect_org_values(
            {"rows": [{"org_id": "1"}, {"org_id": "2"}, {"foo": "bar"}]}
        ) == ["1", "2"]

    def test_list_of_dicts_under_org_key(self) -> None:
        """org_id maps to list of dicts; scalars skipped, inner org_id collected."""
        assert guardrail_plugin._collect_org_values(
            {"org_id": [{"org_id": "inner-1"}, {"org_id": "inner-2"}]}
        ) == ["inner-1", "inner-2"]

    def test_mixed_list_scalars_and_dicts_under_org_id(self) -> None:
        assert guardrail_plugin._collect_org_values(
            {"org_id": ["top", {"org_id": "deep"}]}
        ) == ["top", "deep"]

    def test_plain_list_of_strings_without_org_named_key(self) -> None:
        assert guardrail_plugin._collect_org_values({"hosts": ["h1", "h2"]}) == []

    def test_root_level_list_of_dicts(self) -> None:
        """Arguments are not always a dict at root; still walk list elements."""
        assert guardrail_plugin._collect_org_values(
            [{"organization_id": "x"}, {"rh_org_id": "y"}]
        ) == ["x", "y"]


@pytest.fixture
def mock_tool() -> MagicMock:
    t = MagicMock()
    t.name = "test__example_tool"
    return t


@pytest.fixture
def mock_tool_context() -> MagicMock:
    ctx = MagicMock()
    ctx.invocation_id = "inv-test-1"
    return ctx


@pytest.mark.asyncio
async def test_guardrail_disabled_allows_mismatching_org(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    """When guardrail_org_args_enabled is false, org mismatch is not blocked."""
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = False
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "org-token")

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"org_id": "other-org"},
        tool_context=mock_tool_context,
    )
    assert result is None


@pytest.mark.asyncio
async def test_guardrail_no_org_args_allows(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "org-token")

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"host_id": "abc-123"},
        tool_context=mock_tool_context,
    )
    assert result is None


@pytest.mark.asyncio
async def test_guardrail_matching_org_allowed(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "org-42")

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"org_id": "org-42", "filters": {"org_id": "org-42"}},
        tool_context=mock_tool_context,
    )
    assert result is None


@pytest.mark.asyncio
async def test_guardrail_mismatch_blocked(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "org-42")

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"organization_id": "org-999"},
        tool_context=mock_tool_context,
    )
    assert result is not None
    assert result.get("blocked") is True
    assert result.get("code") == "guardrail_org_mismatch"
    assert "error" in result


@pytest.mark.asyncio
async def test_guardrail_no_request_org_blocks_when_args_have_org(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: None)

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"rh_org_id": "any"},
        tool_context=mock_tool_context,
    )
    assert result is not None
    assert result.get("blocked") is True
    assert result.get("code") == "guardrail_org_mismatch"
    assert "error" in result


@pytest.mark.asyncio
async def test_guardrail_hyphenated_key_normalized(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "same")

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"Org-Id": "other"},  # normalized to org_id
        tool_context=mock_tool_context,
    )
    assert result is not None
    assert result.get("blocked") is True


@pytest.mark.asyncio
async def test_guardrail_org_id_list_all_must_match_token(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    """org_id may be a list of scalars; each entry is checked against JWT org_id."""
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "111")

    plugin = GuardrailPlugin()
    ok = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"org_id": ["111", "111"]},
        tool_context=mock_tool_context,
    )
    assert ok is None

    blocked = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"org_id": ["111", "222"]},
        tool_context=mock_tool_context,
    )
    assert blocked is not None
    assert blocked.get("blocked") is True


@pytest.mark.asyncio
async def test_guardrail_numeric_org_id(
    monkeypatch: pytest.MonkeyPatch,
    mock_tool: MagicMock,
    mock_tool_context: MagicMock,
) -> None:
    mock_settings = MagicMock()
    mock_settings.guardrail_org_args_enabled = True
    monkeypatch.setattr(guardrail_plugin, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(guardrail_plugin, "get_request_org_id", lambda: "3340851")

    plugin = GuardrailPlugin()
    result = await plugin.before_tool_callback(
        tool=mock_tool,
        tool_args={"org_id": 3340851},
        tool_context=mock_tool_context,
    )
    assert result is None

