from aigp_evidence import EvidenceReporter, Settings


def test_evidence_is_disabled_when_no_settings_are_supplied() -> None:
    reporter = EvidenceReporter("execution-1", None)
    reporter.start(agent_id="agent", agent_name="Agent", workflow="test")
    reporter.event(
        "MODEL_CALL",
        actor_type="MODEL",
        actor_id="model",
        attributes={"ordinal": 1},
        key="model-1",
    )
    reporter.complete("SUCCEEDED")
    assert reporter.status == "disabled"
    assert reporter.execution_id is None


def test_settings_keep_access_token_optional() -> None:
    settings = Settings(
        base_url="http://localhost:8000",
        organization_id="org_default",
        project_id="project_default",
        access_token=None,
    )
    assert settings.base_url == "http://localhost:8000"
    assert settings.access_token is None
