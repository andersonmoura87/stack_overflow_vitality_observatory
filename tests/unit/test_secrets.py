from stackoverflow_vitality.ingestion.secrets import ExecutionSecret


def test_secret_loaded_once_per_execution() -> None:
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return "value"

    secret = ExecutionSecret(loader)
    assert secret.get() == "value"
    assert secret.get() == "value"
    assert calls == 1
