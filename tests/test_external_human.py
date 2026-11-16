"""Format 1.3: sets made by people outside the consumer, as input to check."""

from pathlib import Path
from typing import Any

import pytest
from builders import CREATED, sample

import ontospan
from ontospan.models import AnnotatedDocument

SOURCE = {
    "name": "Example Hospital re-annotation 2026",
    "description": "Two clinicians annotated the texts in another tool.",
    "producer": {"name": "example-annotator", "version": "4.2"},
}


def external(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = sample().model_dump(mode="json")
    data["annotation_set"]["set_kind"] = "external_human"
    data["annotation_set"]["source"] = dict(SOURCE)
    data["annotation_set"].update(changes)
    return data


def error_codes(data: dict[str, Any]) -> list[str]:
    return [issue.code for issue in ontospan.validate(data).errors]


def test_an_external_human_set_round_trips_with_its_source() -> None:
    loaded = ontospan.load_annotated_document(external())
    assert loaded.schema_version == "1.3.0"
    assert loaded.annotation_set.set_kind == "external_human"
    source = loaded.annotation_set.source
    assert isinstance(source, ontospan.ExternalSource)
    assert source.name == SOURCE["name"]
    assert source.producer == ontospan.Producer(name="example-annotator", version="4.2")
    assert ontospan.load_annotated_document(ontospan.dump_json(loaded)) == loaded


@pytest.mark.parametrize("name", ["bundle", "bundle.zip"])
def test_an_external_human_set_survives_a_bundle(tmp_path: Path, name: str) -> None:
    item = AnnotatedDocument.model_validate(external())
    manifest = ontospan.write_bundle(
        tmp_path / name, [item], bundle_id="b", created_at=CREATED
    )
    assert manifest.schema_version == "1.3.0"
    assert ontospan.read_bundle(tmp_path / name) == (manifest, [item])


@pytest.mark.parametrize("method", ["manual", "adopted", "edited", "merged"])
def test_human_methods_are_allowed(method: str) -> None:
    data = external()
    data["annotation_set"]["annotations"][0]["provenance"]["method"] = method
    assert error_codes(data) == []


def test_the_source_is_required() -> None:
    data = external()
    del data["annotation_set"]["source"]
    assert error_codes(data) == ["external_source_missing"]
    data["annotation_set"]["source"] = None
    assert error_codes(data) == ["external_source_missing"]


def test_the_source_needs_a_name() -> None:
    assert error_codes(external(source={"name": ""})) == ["schema"]
    assert error_codes(external(source={"description": "x"})) == ["schema"]


def test_every_actor_is_human() -> None:
    data = external()
    data["annotation_set"]["actors"][0]["kind"] = "tool"
    provenance = data["annotation_set"]["annotations"][1]["provenance"]
    provenance["actor"]["kind"] = "tool"
    report = ontospan.validate(data)
    assert [(issue.code, issue.path) for issue in report.errors] == [
        ("external_actor_kind", "annotation_set.actors[0].kind"),
        ("external_actor_kind", "annotation_set.annotations[1].provenance.actor.kind"),
    ]


def test_method_tool_is_not_allowed() -> None:
    data = external()
    data["annotation_set"]["annotations"][0]["provenance"]["method"] = "tool"
    assert error_codes(data) == ["external_method"]


def test_an_external_set_claims_no_stage() -> None:
    assert error_codes(external(stage="adjudication")) == ["external_stage"]


@pytest.mark.parametrize(
    "kind", ["individual", "stage_result", "adjudicated", "release", "tool_output"]
)
def test_the_source_block_belongs_to_external_sets_only(kind: str) -> None:
    assert error_codes(external(set_kind=kind)) == ["external_source_unexpected"]


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0"])
def test_earlier_versions_know_neither_the_kind_nor_the_source(version: str) -> None:
    data = external()
    data["schema_version"] = version
    assert set(error_codes(data)) == {"schema"}
    del data["annotation_set"]["source"]
    assert error_codes(data) == ["schema"]
    data["annotation_set"]["set_kind"] = "individual"
    assert error_codes(data) == []


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
def test_other_sets_are_written_without_a_source(version: str) -> None:
    item = sample().model_copy(update={"schema_version": version})
    raw = ontospan.dump_json(item)
    assert b'"source"' not in raw
    assert ontospan.load_annotated_document(raw) == item
