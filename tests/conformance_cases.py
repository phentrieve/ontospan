"""The conformance fixtures in ``conformance/``: data plus expected issue codes.

They are part of the binding contract (T1 F12, changes after the plan
review): any implementation of the format must report the same error and
warning codes for these files, validated without an ontology.

Regenerate them with ``uv run python tests/conformance_cases.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from builders import (
    CREATED,
    HPO_LAYER,
    MACHINE_TRANSLATION,
    SEIZURE,
    SEVERE,
    TEXT,
    annotation,
    axes,
    corrected_sample,
    sample,
    translated_sample,
    unit,
    with_variant,
)

from ontospan.models import (
    Actor,
    AxisValue,
    ExternalSource,
    FileEntry,
    Layer,
    Manifest,
    OntologyResource,
    Producer,
    ReleaseInfo,
    ReviewResolution,
    Variant,
)

ROOT = Path(__file__).resolve().parent.parent / "conformance"
CHECKED = ["hierarchy_not_checked"]

Data = dict[str, Any]


def _dump(item: Any) -> Data:
    data: Data = item.model_dump(mode="json")
    # Keep the pre-1.2 conformance corpus stable as historical evidence.
    data["schema_version"] = "1.1.0"
    return data


def _sample() -> Data:
    return _dump(sample())


def _review_resolution(*, note: str | None = "Source evidence needs review.") -> Data:
    data = sample().model_dump(mode="json")
    # Written when 1.2 was current; the fixtures stay as they were.
    data["schema_version"] = "1.2.0"
    data["annotation_set"]["annotations"][1]["review_resolution"] = ReviewResolution(
        outcome="uncertain",
        reviewer=Actor(actor_id="reviewer-1", kind="human"),
        note=note,
    ).model_dump(mode="json")
    return data


def _invalid_review_resolution(change: Callable[[Data], None]) -> Callable[[], Data]:
    def build() -> Data:
        data = _review_resolution()
        change(data["annotation_set"]["annotations"][1]["review_resolution"])
        return data

    return build


EXTERNAL_SOURCE = ExternalSource(
    name="Example Hospital re-annotation 2026",
    description="Two clinicians annotated the texts in another tool.",
    producer=Producer(name="example-annotator", version="4.2"),
)


def _external_human(change: Callable[[Data], None] | None = None) -> Callable[[], Data]:
    """A format 1.3 set made by people outside the consumer."""

    def build() -> Data:
        data = sample().model_dump(mode="json")
        data["schema_version"] = "1.3.0"
        data["annotation_set"]["set_kind"] = "external_human"
        data["annotation_set"]["source"] = EXTERNAL_SOURCE.model_dump(mode="json")
        if change is not None:
            change(data["annotation_set"])
        return data

    return build


def _source_on_individual() -> Data:
    data = _external_human()()
    data["annotation_set"]["set_kind"] = "individual"
    return data


def _external_human_before_1_3() -> Data:
    data = _external_human()()
    data["schema_version"] = "1.2.0"
    del data["annotation_set"]["source"]
    return data


def _first_provenance(annotation_set: Data) -> Data:
    provenance: Data = annotation_set["annotations"][0]["provenance"]
    return provenance


def _discontinuous() -> Data:
    return _dump(unit([annotation("a1", SEIZURE, [[(55, 62), (63, 76)]])]))


def _document_level() -> Data:
    return _dump(unit([annotation("a1", SEIZURE, [], document_level=True)]))


def _generic_layer() -> Data:
    layer = Layer(
        layer_id="diagnosis",
        profile="generic",
        ontology=OntologyResource(id="mondo", version="2026-06-01"),
    )
    free = {"certainty": AxisValue(value="confirmed", basis="chosen")}
    item = annotation(
        "d1", "MONDO:0005027", [(63, 76)], axes_=free, layer_id="diagnosis"
    )
    return _dump(unit([item], layers=[layer]))


def _newer_minor() -> Data:
    data = _sample()
    data["schema_version"] = "1.4.0"
    data["annotation_set"]["relations"] = [{"from": "a1", "to": "a2"}]
    data["annotation_set"]["layers"].append(
        {
            "layer_id": "maxo",
            "profile": "medical-action",
            "ontology": {"id": "maxo", "version": "2026-01-01"},
        }
    )
    return data


def _changed(change: Callable[[Data], None]) -> Callable[[], Data]:
    def build() -> Data:
        data = _sample()
        change(data)
        return data

    return build


def _first_segment(data: Data) -> Data:
    segment: Data = data["annotation_set"]["annotations"][0]["mentions"][0]["segments"][
        0
    ]
    return segment


def _hpo_axes() -> Data:
    bad = {**axes(), "temporality": AxisValue(value="future", basis="chosen")}
    del bad["experiencer"]
    return _dump(unit([annotation("a1", SEIZURE, [(63, 76)], axes_=bad)]))


def _source_ref() -> Data:
    item = annotation("a1", SEIZURE, [(63, 76)], method="adopted", source_refs=["a1"])
    return _dump(unit([item]))


def _manifest_missing_version() -> Data:
    data = _dump(
        Manifest(
            bundle_id="b",
            created_at=CREATED,
            layers=(HPO_LAYER,),
            files=(FileEntry(path="documents/0.json", sha256="0" * 64),),
        )
    )
    del data["schema_version"]
    return data


def _manifest_missing_artifact_type() -> Data:
    data = _manifest_missing_version()
    data["schema_version"] = "1.0.0"
    del data["artifact_type"]
    return data


def _variant_missing_derived_from() -> Data:
    variant = Variant(version=1, origin="machine_translation")
    return _dump(with_variant(sample(), variant, MACHINE_TRANSLATION))


def _translation_on_native() -> Data:
    variant = Variant(version=1, origin="native")
    return _dump(with_variant(sample(), variant, MACHINE_TRANSLATION))


def _correction_with_translation(version: str) -> Callable[[], Data]:
    """A corrected text with the run that produced it (allowed from 1.3)."""

    def build() -> Data:
        corrected = corrected_sample()
        item = with_variant(corrected, corrected.document.variant, MACHINE_TRANSLATION)
        data = item.model_dump(mode="json")
        data["schema_version"] = version
        return data

    return build


def _translation_on_native_1_3() -> Data:
    data = _translation_on_native()
    data["schema_version"] = "1.3.0"
    return data


def _manifest_release_name_mismatch() -> Data:
    return _dump(
        Manifest(
            bundle_id="b",
            release_name="Gold v1",
            release=ReleaseInfo(name="Gold v2"),
            created_at=CREATED,
            layers=(HPO_LAYER,),
            files=(FileEntry(path="documents/0.json", sha256="0" * 64),),
        )
    )


# name -> (builder, errors, warnings, valid under the hpo profile schema or None)
CASES: dict[str, tuple[Callable[[], Data], list[str], list[str], bool | None]] = {
    "valid/correction-with-translation": (
        _correction_with_translation("1.3.0"),
        [],
        CHECKED,
        True,
    ),
    "invalid/correction-with-translation-before-1-3": (
        _correction_with_translation("1.2.0"),
        ["translation_unexpected"],
        CHECKED,
        True,
    ),
    "invalid/translation-on-native-1-3": (
        _translation_on_native_1_3,
        ["translation_unexpected"],
        CHECKED,
        True,
    ),
    "valid/external-human": (_external_human(), [], CHECKED, True),
    "valid/external-human-source-name-only": (
        _external_human(
            lambda s: s.update(source={"name": "Example Hospital re-annotation 2026"})
        ),
        [],
        CHECKED,
        True,
    ),
    "invalid/external-human-missing-source": (
        _external_human(lambda s: s.pop("source")),
        ["external_source_missing"],
        CHECKED,
        True,
    ),
    "invalid/external-human-stage": (
        _external_human(lambda s: s.update(stage="adjudication")),
        ["external_stage"],
        CHECKED,
        True,
    ),
    "invalid/external-human-tool-actor": (
        _external_human(
            lambda s: s["actors"].append(
                {"actor_id": "tool-1", "kind": "tool", "name": "t", "version": "1"}
            )
        ),
        ["external_actor_kind"],
        CHECKED,
        True,
    ),
    "invalid/external-human-tool-annotation": (
        _external_human(lambda s: _first_provenance(s)["actor"].update(kind="tool")),
        ["external_actor_kind"],
        CHECKED,
        True,
    ),
    "invalid/external-human-method-tool": (
        _external_human(lambda s: _first_provenance(s).update(method="tool")),
        ["external_method"],
        CHECKED,
        True,
    ),
    "invalid/external-human-source-unknown-field": (
        _external_human(lambda s: s["source"].update(url="https://example.org")),
        ["schema"],
        [],
        None,
    ),
    "invalid/external-human-before-1-3": (
        _external_human_before_1_3,
        ["schema"],
        [],
        None,
    ),
    "invalid/source-on-other-set-kind": (
        _source_on_individual,
        ["external_source_unexpected"],
        CHECKED,
        True,
    ),
    "valid/review-resolution": (_review_resolution, [], CHECKED, True),
    "valid/review-resolution-without-note": (
        lambda: _review_resolution(note=None),
        [],
        CHECKED,
        True,
    ),
    "invalid/review-resolution-outcome": (
        _invalid_review_resolution(lambda r: r.update(outcome="confirmed")),
        ["schema"],
        [],
        None,
    ),
    "invalid/review-resolution-reviewer": (
        _invalid_review_resolution(lambda r: r.pop("reviewer")),
        ["schema"],
        [],
        None,
    ),
    "invalid/review-resolution-unknown-field": (
        _invalid_review_resolution(lambda r: r.update(status="pending")),
        ["schema"],
        [],
        None,
    ),
    "invalid/document-missing-artifact-type": (
        _changed(lambda d: d.pop("artifact_type")),
        ["schema"],
        [],
        None,
    ),
    "invalid/manifest-missing-artifact-type": (
        _manifest_missing_artifact_type,
        ["schema"],
        [],
        None,
    ),
    "invalid/unicode-surrogate": (
        _changed(lambda d: d["document"].update(text="\ud800")),
        ["invalid_unicode"],
        [],
        None,
    ),
    "invalid/document-missing-schema-version": (
        _changed(lambda d: d.pop("schema_version")),
        ["schema_version"],
        [],
        None,
    ),
    "invalid/manifest-missing-schema-version": (
        _manifest_missing_version,
        ["schema_version"],
        [],
        None,
    ),
    "valid/sample": (_sample, [], CHECKED, True),
    "valid/discontinuous-mention": (_discontinuous, [], CHECKED, True),
    "valid/document-level": (_document_level, [], CHECKED, True),
    "valid/generic-layer": (_generic_layer, [], [], None),
    "valid/newer-minor": (
        _newer_minor,
        [],
        ["newer_minor", "hierarchy_not_checked", "unknown_profile"],
        True,
    ),
    "invalid/unsupported-major": (
        _changed(lambda d: d.update(schema_version="2.0.0")),
        ["unsupported_major"],
        [],
        None,
    ),
    "invalid/unknown-field": (
        _changed(lambda d: d["document"].update(title="x")),
        ["schema"],
        [],
        None,
    ),
    "invalid/timestamp-without-zone": (
        _changed(
            lambda d: d["annotation_set"].update(created_at="2026-10-14T12:00:00")
        ),
        ["schema"],
        [],
        None,
    ),
    "invalid/text-not-canonical": (
        _changed(lambda d: d["document"].update(text=TEXT + "\r\n")),
        ["text_not_canonical", "text_hash"],
        CHECKED,
        True,
    ),
    "invalid/segment-text-mismatch": (
        _changed(lambda d: _first_segment(d).update(text="Krampfanfalle")),
        ["segment_text_mismatch"],
        CHECKED,
        True,
    ),
    "invalid/segment-out-of-range": (
        _changed(lambda d: _first_segment(d).update(start=70, end=83)),
        ["segment_out_of_range"],
        CHECKED,
        True,
    ),
    "invalid/segments-unordered": (
        lambda: _dump(unit([annotation("a1", SEIZURE, [[(63, 76), (55, 62)]])])),
        ["segments_unordered"],
        CHECKED,
        True,
    ),
    "invalid/unknown-layer": (
        lambda: _dump(unit([annotation("a1", SEIZURE, [(63, 76)], layer_id="x")])),
        ["unknown_layer"],
        CHECKED,
        None,
    ),
    "invalid/hpo-axes": (_hpo_axes, ["axes", "axis_value"], CHECKED, False),
    "invalid/hpo-missing-evidence": (
        lambda: _dump(unit([annotation("a1", SEIZURE, [])])),
        ["missing_evidence"],
        CHECKED,
        False,
    ),
    "invalid/hpo-not-an-hpo-id": (
        lambda: _dump(unit([annotation("a1", "MONDO:0005027", [(63, 76)])])),
        ["not_an_hpo_id"],
        CHECKED,
        False,
    ),
    "invalid/hpo-modifier-id": (
        lambda: _dump(
            unit(
                [annotation("a1", SEIZURE, [(63, 76)], modifiers=[(f"X{SEVERE}", [])])]
            )
        ),
        ["not_an_hpo_id"],
        CHECKED,
        False,
    ),
    "invalid/source-ref-syntax": (_source_ref, ["source_ref_syntax"], CHECKED, True),
    "valid/format-1-0": (
        _changed(lambda d: d.update(schema_version="1.0.0")),
        [],
        CHECKED,
        True,
    ),
    "valid/format-1-1": (_sample, [], CHECKED, True),
    "valid/variant-translation": (
        lambda: _dump(translated_sample()),
        [],
        CHECKED,
        True,
    ),
    "valid/variant-correction": (lambda: _dump(corrected_sample()), [], CHECKED, True),
    "invalid/variant-missing-derived-from": (
        _variant_missing_derived_from,
        ["variant_derived_from_missing"],
        CHECKED,
        True,
    ),
    "invalid/translation-on-native": (
        _translation_on_native,
        ["translation_unexpected"],
        CHECKED,
        True,
    ),
    "invalid/manifest-release-name-mismatch": (
        _manifest_release_name_mismatch,
        ["release_name_mismatch"],
        [],
        None,
    ),
}


def render(data: Any) -> str:
    rendered = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        rendered.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    return rendered


def expected() -> Data:
    """The content of ``conformance/cases.json``."""
    return {
        "cases": [
            {
                "file": f"{name}.json",
                "artifact_type": "bundle"
                if name.startswith("invalid/manifest-")
                else "annotated_document",
                "errors": errors,
                "warnings": warnings,
                "hpo_profile_schema": profile,
            }
            for name, (_, errors, warnings, profile) in CASES.items()
        ]
    }


def files() -> dict[str, str]:
    """Every fixture file by its path below ``conformance/``."""
    out = {f"{name}.json": render(build()) for name, (build, *_) in CASES.items()}
    out["cases.json"] = render(expected())
    return out


def main() -> None:
    for name, content in files().items():
        target = ROOT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
