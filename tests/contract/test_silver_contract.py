from copy import deepcopy

import pytest

from stackoverflow_vitality.domain.serialization import to_record
from stackoverflow_vitality.silver.models import ContractError, validate_history, validate_record
from tests.silver_fixtures import normalized


@pytest.mark.parametrize(
    "field",
    [
        "question_id",
        "schema_version",
        "source_run_id",
        "source_page",
        "source_checksum",
        "body_html",
        "valid_from",
    ],
)
def test_missing_contract_field_fails(field):
    record = to_record(normalized())
    del record[field]
    with pytest.raises(ContractError):
        validate_record(record)


@pytest.mark.parametrize(
    "field,value",
    [
        ("question_id", "123"),
        ("question_id", True),
        ("schema_version", None),
        ("source_page", None),
        ("source_run_id", ""),
        ("is_current", 1),
        ("tags", "python"),
        ("creation_at", "2026-01-01"),
        ("record_hash", "f" * 64),
    ],
)
def test_incompatible_contract_type_or_lineage_fails(field, value):
    record = to_record(normalized())
    record[field] = value
    with pytest.raises(ContractError):
        validate_record(record)


def test_multiple_current_versions_rejected():
    record = to_record(normalized())
    with pytest.raises(ContractError):
        validate_history([record, deepcopy(record)])
