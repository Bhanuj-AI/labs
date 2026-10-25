from pathlib import Path

from banking import BankingStore


def test_seed_and_idempotent_recovery_boundary(tmp_path: Path) -> None:
    store = BankingStore(tmp_path / "banking.db")
    store.seed()
    assert store.status() == "PENDING"
    first = store.approve("mutation-1")
    retry = store.approve("mutation-1")
    assert (first.applied, retry.applied, store.status()) == (True, False, "APPROVED")
