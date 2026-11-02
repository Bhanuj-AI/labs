from policy import TOOL, lookup_policy


def test_tool_schema_requires_only_policy_id() -> None:
    assert TOOL["name"] == "lookup_policy"
    assert TOOL["input_schema"]["required"] == ["policy_id"]
    assert TOOL["input_schema"]["additionalProperties"] is False


def test_known_policy_is_a_deterministic_application_result() -> None:
    result = lookup_policy("POL-1001")
    assert result["ok"] is True
    assert result["reason_code"] == "POLICY_FOUND"
    assert "status=ACTIVE" in result["content"]


def test_missing_policy_is_an_explicit_tool_failure() -> None:
    result = lookup_policy("POL-404")
    assert result == {
        "ok": False,
        "reason_code": "POLICY_NOT_FOUND",
        "content": "Policy POL-404 was not found.",
    }
