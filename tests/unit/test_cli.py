import json

from stackoverflow_vitality.cli import main


def test_dry_run_validates_without_network(capsys, monkeypatch) -> None:
    monkeypatch.delenv("STACKEXCHANGE_API_KEY", raising=False)
    exit_code = main(
        [
            "collect-questions",
            "--tag",
            "python",
            "--from",
            "2026-09-01T00:00:00Z",
            "--to",
            "2026-09-02T00:00:00Z",
            "--max-pages",
            "2",
            "--dry-run",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["tag"] == "python"
    assert output["max_pages"] == 2
    assert output["status"] == "dry_run"


def test_dry_run_rejects_naive_timestamp() -> None:
    try:
        main(
            [
                "collect-questions",
                "--tag",
                "python",
                "--from",
                "2026-09-01T00:00:00",
                "--to",
                "2026-09-02T00:00:00Z",
                "--dry-run",
            ]
        )
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("naive timestamps must be rejected")
