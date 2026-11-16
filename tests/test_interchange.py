import json
import xml.etree.ElementTree as ET
from typing import Any

import pytest
from builders import FIXTURES, SEIZURE, SEVERE, annotation, axes, unit

import ontospan
from ontospan.bioc import to_bioc_json, to_bioc_xml
from ontospan.brat import to_brat
from ontospan.models import (
    Actor,
    AnnotatedDocument,
    Annotation,
    AnnotationSet,
    ReviewResolution,
)
from ontospan.ontology import ObographOntology
from ontospan.text import canonical_text

# Emoji are one codepoint, two UTF-16 units and four UTF-8 bytes.
# Krampfanfälle 2-15, schwere 19-26, Hypotonie 27-36, Fieber 41-47.
T = "🧒 Krampfanfälle, 😷 schwere\nHypotonie und Fieber."
HYPOTONIA, FEVER, DIALEPTIC = "HP:0001252", "HP:0001945", "HP:0011146"


def a(aid: str, term: str, *spans: Any, **kw: Any) -> Annotation:
    return annotation(aid, term, list(spans), text=T, **kw)


def items() -> list[Annotation]:
    return [
        a("a1", SEIZURE, (2, 15), cues=[(0, 1)], modifiers=[(SEVERE, [(19, 26)])]),
        a("a2", HYPOTONIA, [(19, 26), (27, 36)]),
        a("a3", HYPOTONIA, (27, 36), axes_=axes("absent", basis="defaulted")),
        a("a4", FEVER, (41, 47), (2, 15)),
        a("a5", HYPOTONIA, (19, 36)),
        a("d1", DIALEPTIC, document_level=True),
    ]


def sample(annotations: list[Annotation] | None = None, **kw: Any) -> AnnotatedDocument:
    return unit(items() if annotations is None else annotations, text=T, **kw)


@pytest.fixture(scope="module")
def hp() -> ObographOntology:
    return ObographOntology.load(FIXTURES / "hp-mini.json")


def reversed_sample() -> AnnotatedDocument:
    item = sample()
    annotation_set = AnnotationSet.model_validate(
        {
            **item.annotation_set.model_dump(),
            "annotations": list(reversed(item.annotation_set.annotations)),
        }
    )
    return item.model_copy(update={"annotation_set": annotation_set})


# brat


def entities(ann: str) -> list[tuple[str, str, list[tuple[int, int]], str]]:
    found = []
    for line in ann.splitlines():
        if line.startswith("T"):
            entity, middle, quoted = line.split("\t")
            kind, spans = middle.split(" ", 1)
            fragments = [
                (int(start), int(end))
                for start, end in (part.split(" ") for part in spans.split(";"))
            ]
            found.append((entity, kind, fragments, quoted))
    return found


def test_brat_writes_codepoint_offsets_terms_attributes_and_notes(
    hp: ObographOntology,
) -> None:
    files, report = to_brat(sample([items()[0]]), hp, bundle_id="b-1")
    assert files.txt == T
    assert files.ann == (
        "T1\thpo 2 15\tKrampfanfälle\n"
        "N1\tReference T1 HP:0001250\tSeizure\n"
        "A1\tassertion T1 present\n"
        "A2\texperiencer T1 patient\n"
        "A3\ttemporality T1 current\n"
        "#1\tAnnotatorNotes T1\tref=urn:ontospan:b-1:set-1:a1; mention=1; "
        "modifiers=HP:0012828; annotator=ann-03; annotator_kind=human; "
        "method=manual\n"
    )
    assert (report.cue_segments_dropped, report.modifier_segments_dropped) == (1, 1)
    assert report.axis_basis_dropped == 3


def test_brat_entities_quote_the_text_and_split_at_line_breaks() -> None:
    files, report = to_brat(sample())
    found = entities(files.ann)
    for _, kind, fragments, quoted in found:
        assert kind == "hpo"
        assert quoted == " ".join(files.txt[s:e] for s, e in fragments)
    # Text order, one entity per mention; a2 is discontinuous, a5 crosses "\n".
    assert [fragments for _, _, fragments, _ in found] == [
        [(2, 15)],
        [(2, 15)],
        [(19, 26), (27, 36)],
        [(19, 26), (27, 36)],
        [(27, 36)],
        [(41, 47)],
    ]
    assert "ref=urn:ontospan:set-1:a5; mention=1" in files.ann
    assert "ref=urn:ontospan:set-1:a4; mention=2" in files.ann
    assert report.split_at_line_breaks == 1
    assert (report.exported, report.mentions, report.discontinuous) == (5, 6, 1)


def test_brat_drops_spanless_annotations_into_the_report() -> None:
    files, report = to_brat(sample())
    assert "HP:0011146" not in files.ann
    assert report.spanless == 1
    assert report.dropped == ["urn:ontospan:set-1:d1"]


def test_brat_output_is_deterministic() -> None:
    assert to_brat(sample())[0] == to_brat(reversed_sample())[0]


def test_brat_notes_keep_provenance_and_review_on_one_line() -> None:
    resolution = ReviewResolution(
        outcome="uncertain",
        reviewer=Actor(actor_id="rev-1", kind="human"),
        note="check\nagain",
    )
    item = a("a1", SEIZURE, (2, 15), source_refs=["urn:ontospan:tool:x1"])
    item = item.model_copy(update={"review_resolution": resolution})
    files, report = to_brat(sample([item]))
    note = files.ann.splitlines()[-1]
    assert note.endswith(
        "source_refs=urn:ontospan:tool:x1; review_outcome=uncertain; "
        "reviewer=rev-1; reviewer_kind=human; review_note=check again"
    )
    assert report.note_text_flattened == 1


def test_brat_refuses_names_with_whitespace() -> None:
    with pytest.raises(ValueError, match="whitespace"):
        to_brat(sample([a("a1", SEIZURE, (2, 15), axes_=axes("not sure"))]))


# BioC


def test_bioc_json_has_one_document_with_one_passage() -> None:
    collection, _ = to_bioc_json([sample(subject_id="P1")])
    assert collection["infons"]["offset_unit"] == "codepoint"
    assert (collection["source"], collection["date"]) == ("ontospan", "2026-10-14")
    (document,) = collection["documents"]
    assert document["id"] == "doc-1"
    assert document["infons"]["subject_id"] == "P1"
    assert document["infons"]["layer.hpo.version"] == "2026-06-23"
    (passage,) = document["passages"]
    assert (passage["offset"], passage["text"]) == (0, T)


def test_bioc_annotations_carry_locations_and_infons(hp: ObographOntology) -> None:
    collection, report = to_bioc_json([sample()], hp, bundle_id="b-1")
    annotations = collection["documents"][0]["passages"][0]["annotations"]
    assert [item["id"] for item in annotations] == [
        "a1",
        "a4/2",
        "a2",
        "a5",
        "a3",
        "a4/1",
    ]
    for item in annotations:
        quoted = [T[x["offset"] : x["offset"] + x["length"]] for x in item["locations"]]
        assert item["text"] == " ".join(quoted)
    first, _, discontinuous, *_ = annotations
    assert discontinuous["locations"] == [
        {"offset": 19, "length": 7},
        {"offset": 27, "length": 9},
    ]
    assert discontinuous["text"] == "schwere Hypotonie"
    assert first["infons"] == {
        "identifier": SEIZURE,
        "type": "hpo",
        "label": "Seizure",
        "assertion": "present",
        "assertion_basis": "chosen",
        "experiencer": "patient",
        "experiencer_basis": "chosen",
        "temporality": "current",
        "temporality_basis": "chosen",
        "modifiers": SEVERE,
        "annotator": "ann-03",
        "annotator_kind": "human",
        "method": "manual",
        "annotation_ref": "urn:ontospan:b-1:set-1:a1",
        "mention": "1",
    }
    assert annotations[4]["infons"]["assertion_basis"] == "defaulted"
    assert (report.exported, report.mentions, report.discontinuous) == (6, 6, 1)
    assert (report.cue_segments_dropped, report.modifier_segments_dropped) == (1, 1)
    assert report.dropped == []


def test_bioc_spanless_annotations_become_relations_without_nodes() -> None:
    collection, report = to_bioc_json([sample()])
    (relation,) = collection["documents"][0]["relations"]
    assert relation["id"] == "d1"
    assert relation["nodes"] == []
    assert relation["infons"]["identifier"] == DIALEPTIC
    assert relation["infons"]["document_level"] == "true"
    assert report.spanless == 1


def test_bioc_output_is_deterministic_and_json_serialisable() -> None:
    first, _ = to_bioc_json([sample()])
    second, _ = to_bioc_json([reversed_sample()])
    assert json.dumps(first, ensure_ascii=False) == json.dumps(
        second, ensure_ascii=False
    )


def test_bioc_alternative_sets_of_one_document_get_their_own_ids() -> None:
    collection, report = to_bioc_json([sample(set_id="set:A"), sample(set_id="set:B")])
    assert [d["id"] for d in collection["documents"]] == [
        "urn:ontospan:document:doc-1:set%3AA",
        "urn:ontospan:document:doc-1:set%3AB",
    ]
    assert report.exported == 12
    with pytest.raises(ValueError, match="at least one document"):
        to_bioc_json([])


def test_bioc_xml_follows_the_dtd_order_and_the_json_content() -> None:
    xml, report = to_bioc_xml([sample()])
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE')
    root = ET.fromstring(xml.encode("utf-8"))
    assert [child.tag for child in root][:4] == ["source", "date", "key", "infon"]
    document = root.find("document")
    assert document is not None
    assert document[0].tag == "id"
    passage = document.find("passage")
    assert passage is not None
    assert passage.findtext("text") == T
    item = passage.find("annotation[@id='a2']")
    assert item is not None
    assert [child.tag for child in item][-3:] == ["location", "location", "text"]
    assert [(x.get("offset"), x.get("length")) for x in item.iter("location")] == [
        ("19", "7"),
        ("27", "9"),
    ]
    assert item.findtext("infon[@key='identifier']") == HYPOTONIA
    relation = document.find("relation[@id='d1']")
    assert relation is not None
    assert relation.findtext("infon[@key='document_level']") == "true"
    assert root.findtext("infon[@key='offset_unit']") == "codepoint"
    assert report.spanless == 1
    assert to_bioc_xml([reversed_sample()])[0] == xml


def test_bioc_xml_refuses_characters_xml_cannot_hold() -> None:
    plain = a("a1", SEIZURE, (2, 15))
    provenance = plain.provenance.model_copy(update={"notes": ("bell \x07",)})
    item = sample([plain.model_copy(update={"provenance": provenance})])
    collection, _ = to_bioc_json([item])
    infons = collection["documents"][0]["passages"][0]["annotations"][0]["infons"]
    assert infons["note_1"] == "bell \x07"
    with pytest.raises(ValueError, match="XML 1"):
        to_bioc_xml([item])


# BioC: offset units, the list of losses and the golden file

# A ZWJ emoji sequence (4 codepoints, 13 bytes), a combining mark that NFC
# keeps apart (x + U+0304), a precomposed umlaut and an astral letter.
U = "👩‍⚕️ sieht x̄ Krampfanfälle \U0001d4b3 und Fieber."
GOLDEN = FIXTURES / "bioc-sample.json"


def span(word: str) -> tuple[int, int]:
    start = U.index(word)
    return start, start + len(word)


def unicode_sample() -> AnnotatedDocument:
    assert canonical_text(U) == U
    found = [
        annotation("u1", SEIZURE, [span("Krampfanfälle")], text=U),
        annotation("u2", HYPOTONIA, [[span("x̄"), span("\U0001d4b3")]], text=U),
        annotation("u3", FEVER, [span("Fieber")], text=U),
    ]
    return unit(found, text=U)


def bioc_annotations(collection: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = collection["documents"][0]["passages"][0][
        "annotations"
    ]
    return found


def test_bioc_codepoint_offsets_index_the_passage_text() -> None:
    collection, _ = to_bioc_json([unicode_sample()])
    assert collection["infons"]["offset_unit"] == "codepoint"
    text = collection["documents"][0]["passages"][0]["text"]
    assert text == U
    items = {item["id"]: item for item in bioc_annotations(collection)}
    for item in items.values():
        quoted = [
            text[x["offset"] : x["offset"] + x["length"]] for x in item["locations"]
        ]
        assert item["text"] == " ".join(quoted)
    # The emoji sequence counts as 4 and the combining mark as its own unit.
    assert items["u1"]["locations"] == [{"offset": 14, "length": 13}]
    assert items["u2"]["locations"] == [
        {"offset": 11, "length": 2},
        {"offset": 28, "length": 1},
    ]
    assert items["u2"]["text"] == "x̄ \U0001d4b3"


def test_bioc_utf8_offsets_index_the_encoded_passage_text() -> None:
    collection, _ = to_bioc_json([unicode_sample()], offset_unit="utf8")
    assert collection["infons"]["offset_unit"] == "utf8"
    raw = collection["documents"][0]["passages"][0]["text"].encode("utf-8")
    items = {item["id"]: item for item in bioc_annotations(collection)}
    for item in items.values():
        quoted = [
            raw[x["offset"] : x["offset"] + x["length"]].decode("utf-8")
            for x in item["locations"]
        ]
        assert item["text"] == " ".join(quoted)
    # 13 bytes of emoji, 3 of the combining pair, 2 of the umlaut, 4 astral.
    assert items["u1"]["locations"] == [{"offset": 24, "length": 14}]
    assert items["u2"]["locations"] == [
        {"offset": 20, "length": 3},
        {"offset": 39, "length": 4},
    ]
    assert items["u3"]["locations"] == [{"offset": 48, "length": 6}]


def test_bioc_offset_units_agree_on_ascii_and_reject_unknown_units() -> None:
    plain = unit([annotation("a1", SEIZURE, [(6, 12)], text="Keine Krampf.")])
    by_codepoint, _ = to_bioc_json([plain])
    by_byte, _ = to_bioc_json([plain], offset_unit="utf8")
    assert bioc_annotations(by_codepoint) == bioc_annotations(by_byte)
    xml, _ = to_bioc_xml([unicode_sample()], offset_unit="utf8")
    assert '<location offset="24" length="14" />' in xml
    with pytest.raises(ValueError, match="offset unit"):
        to_bioc_json([plain], offset_unit="utf16")  # type: ignore[arg-type]


def test_bioc_report_lists_every_loss_per_annotation() -> None:
    resolution = ReviewResolution(
        outcome="uncertain", reviewer=Actor(actor_id="rev-1", kind="human")
    )
    flagged = a("a6", FEVER, (41, 47), axes_=axes("uncertain")).model_copy(
        update={"review_resolution": resolution}
    )
    _, report = to_bioc_json([sample([*items(), flagged])], bundle_id="b-1")
    assert [(issue.code, issue.path) for issue in report.issues] == [
        ("cue_segments_dropped", "urn:ontospan:b-1:set-1:a1"),
        ("modifiers_as_infon", "urn:ontospan:b-1:set-1:a1"),
        ("modifier_segments_dropped", "urn:ontospan:b-1:set-1:a1"),
        ("discontinuous_text_joined", "urn:ontospan:b-1:set-1:a2"),
        ("assertion_in_infon", "urn:ontospan:b-1:set-1:a3"),
        ("assertion_in_infon", "urn:ontospan:b-1:set-1:a6"),
        ("review_resolution_in_infon", "urn:ontospan:b-1:set-1:a6"),
        ("spanless_as_relation", "urn:ontospan:b-1:set-1:d1"),
    ]
    assert {issue.level for issue in report.issues} == {"warning"}
    assert all(issue.message.startswith("doc-1: ") for issue in report.issues)
    assert report.issues[0].message == "doc-1: 1 cue segment(s) not written"
    assert (report.modifiers_flattened, report.assertion_only_in_infons) == (1, 2)
    assert (report.review_resolutions, report.spanless) == (1, 1)


def test_bioc_report_names_the_document_of_each_loss() -> None:
    first = sample([items()[-1]], document_id="doc-1")
    second = sample([items()[1]], document_id="doc-2", set_id="set-2")
    clean = sample([items()[4]], document_id="doc-3", set_id="set-3")
    _, report = to_bioc_json([first, second, clean])
    assert [(issue.code, issue.path, issue.message) for issue in report.issues] == [
        (
            "spanless_as_relation",
            "urn:ontospan:set-1:d1",
            "doc-1: no text span; written as a relation",
        ),
        (
            "discontinuous_text_joined",
            "urn:ontospan:set-2:a2",
            "doc-2: mention 1 is discontinuous; its text is joined",
        ),
    ]


def test_bioc_json_matches_the_golden_file(hp: ObographOntology) -> None:
    collection, report = to_bioc_json([sample()], hp, bundle_id="b-1")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # The file was written by ontospan 1.2.1; only the versions move on.
    golden["infons"].update(
        ontospan_version=ontospan.__version__, schema_version=ontospan.SCHEMA_VERSION
    )
    assert json.dumps(collection, ensure_ascii=False) == json.dumps(
        golden, ensure_ascii=False
    )
    assert len(report.issues) == 6
