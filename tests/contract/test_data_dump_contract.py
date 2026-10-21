"""Contract checks for bronze_stackexchange_data_dump_posts.v1."""

import json
from pathlib import Path
from uuid import uuid4

from stackoverflow_vitality.data_dump.ingest import ingest_data_dump
from tests.dump_fixtures import NOW, answer, clock, config, write_posts

LINE_KEYS = {
    "schema_version",
    "source",
    "source_kind",
    "site",
    "ingestion_identity",
    "post_id",
    "post_type_id",
    "source_row_number",
    "source_file_name",
    "source_sha256",
    "source_file_size",
    "dump_publication_id",
    "parser_version",
    "contract_version",
    "parent_id",
    "accepted_answer_id",
    "source_created_at",
    "score",
    "body",
    "title",
    "tags_raw",
    "owner_user_id",
    "content_license",
    "last_edit_at",
    "last_activity_at",
    "closed_at",
    "view_count",
    "answer_count",
    "comment_count",
}
FORBIDDEN = {
    "published_at",
    "run_started_at",
    "source_file_observed_at",
    "dump_cutoff",
    "transformation_run_id",
    "OwnerDisplayName",
}


def test_line_manifest_and_read_order(tmp_path: Path) -> None:
    source = tmp_path / "Posts.xml"
    write_posts(
        source,
        [
            answer(8, 1),
            'Id="1" PostTypeId="1" Body="Question body" Title="title" Tags="&lt;python&gt;"',
        ],
    )
    loaded = config(tmp_path, dump_cutoff="2024-01-01T00:00:00Z", project_revision="rev-1")
    result = ingest_data_dump(
        loaded, source, (1,), run_id=str(uuid4()), now=clock, disk_free=lambda _path: 10**12
    )
    shard = next((tmp_path / "bronze").rglob("shard=*.jsonl"))
    raw = shard.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.endswith(b"\n")
    lines = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    assert [line["post_id"] for line in lines] == [8, 1]
    assert set(lines[1]) == LINE_KEYS
    assert lines[1]["score"] is None
    assert lines[1]["owner_user_id"] is None
    assert lines[1]["source_created_at"] is None
    assert lines[1]["tags_raw"] == "<python>"
    assert FORBIDDEN.isdisjoint(lines[1])
    assert lines[0]["source_created_at"].endswith("Z")
    assert result["schema_version"] == "bronze_stackexchange_data_dump_posts_manifest.v1"
    assert result["dump_cutoff_status"] == "operator_declared"
    assert result["dump_cutoff_status"] != "verified"
    assert result["transformation_run_id"] is None
    assert result["project_revision"] == "rev-1"
    assert result["published_at"].endswith("Z")
    assert result["source_file_observed_at"].endswith("Z")
    assert result["run_started_at"].endswith("Z")
    assert result["published_at"] != lines[0]["source_created_at"]
    assert result["source_sha256"] == result["source_sha256_after"]
    assert result["mode"] == "restricted"
    assert result["api_comparison"] == "skipped"
    assert str(source.resolve()) not in json.dumps(result)
    assert result["source_file_observed_at"] == NOW.isoformat().replace("+00:00", "Z")
    other = tmp_path / "other"
    other.mkdir()
    copied = other / "Posts.xml"
    copied.write_bytes(source.read_bytes())
    again = ingest_data_dump(
        config(other, dump_cutoff="2024-01-01T00:00:00Z", project_revision="rev-1"),
        copied,
        (1,),
        run_id=str(uuid4()),
        now=clock,
        disk_free=lambda _path: 10**12,
    )
    assert again["source_identity"] == result["source_identity"]
    assert again["ingestion_identity"] == result["ingestion_identity"]
    assert next((other / "bronze").rglob("shard=*.jsonl")).read_bytes() == raw
