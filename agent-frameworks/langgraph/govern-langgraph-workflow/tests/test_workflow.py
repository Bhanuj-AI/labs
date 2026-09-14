from workflow import build_workflow


def run_claim(*, policy_active: bool, damage_verified: bool) -> dict[str, object]:
    return build_workflow().invoke(
        {
            "claim": {
                "claim_id": "CLM-TEST",
                "customer_id": "CUST-TEST",
                "claim_amount": 2400,
                "policy_active": policy_active,
                "damage_verified": damage_verified,
            }
        }
    )


def test_active_policy_with_verified_damage_is_approved() -> None:
    assert run_claim(policy_active=True, damage_verified=True)["decision"] == "APPROVED"


def test_inactive_policy_is_rejected() -> None:
    assert run_claim(policy_active=False, damage_verified=True)["decision"] == "REJECTED"


def test_unverified_damage_is_rejected() -> None:
    assert run_claim(policy_active=True, damage_verified=False)["decision"] == "REJECTED"
