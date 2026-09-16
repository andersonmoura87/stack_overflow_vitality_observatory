from datetime import UTC, datetime

import pytest

from stackoverflow_vitality.domain.models import CollectionRequest, CollectionWindow, SourceMetadata
from stackoverflow_vitality.domain.serialization import to_record

UTC = UTC


def request(tag: str = "python") -> CollectionRequest:
    return CollectionRequest(
        "stackoverflow",
        "https://api.stackexchange.com/2.3/questions",
        tag,
        CollectionWindow(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)),
    )


def test_accepts_one_tag() -> None:
    assert request().tag_or_group == "python"


def test_rejects_implicit_and_tag_list() -> None:
    with pytest.raises(ValueError, match="AND"):
        request("python;machine-learning")


def test_rejects_invalid_window_and_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="start"):
        CollectionWindow(datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone"):
        CollectionWindow(datetime(2025, 1, 1), datetime(2025, 1, 2, tzinfo=UTC))


def test_serializes_utc_deterministically() -> None:
    metadata = SourceMetadata(
        "stackexchange_api",
        "https://api.stackexchange.com/2.3/questions",
        "stackoverflow",
        "python",
        CollectionWindow(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)),
        1,
        "default",
        datetime(2025, 1, 1, 12, tzinfo=UTC),
    )
    assert to_record(metadata)["collected_at"] == "2025-01-01T12:00:00Z"
    assert list(to_record(metadata)) == sorted(to_record(metadata))
