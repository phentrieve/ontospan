"""BioC export, JSON and XML: derived, lossy, with a report of what was left out.

One BioC document per annotated document, with one passage (offset 0) that
holds the full canonical text: the format has no document sections to follow.

Offsets are Unicode codepoints of that text by default, like ontospan offsets
and like the BioC tools written in Python or Java. ``BioC.dtd`` recommends
UTF-8 bytes instead; ``offset_unit="utf8"`` writes those. The collection
states the unit in the infon ``offset_unit`` (``codepoint`` or ``utf8``).

- One BioC annotation per mention; a discontinuous mention has one location
  per segment and the segment texts joined by a space as its text.
- Annotation infons: ``identifier`` (term), ``type`` (layer), ``label`` (with
  an ontology), the axes (``<axis>`` and ``<axis>_basis``), ``modifiers``
  (space-separated terms), provenance (``annotator``, ``annotator_kind``,
  ``method``, ``source_refs``, ``note_<n>``), reviewer resolution
  (``review_outcome``, ``reviewer``, ``reviewer_kind``, ``review_note``) and
  ``annotation_ref`` and ``mention`` (index from 1) to find the source.
- Annotation ids are the annotation id, with ``/<mention>`` appended when an
  annotation has several mentions.
- An annotation without mentions (document level) has no location, which a
  BioC annotation needs. It becomes a document relation without nodes, with
  the same infons and ``document_level: true``.
- Cue segments and modifier segments are not written (see the report).

What is kept exactly: the text, every mention segment as a location, term,
layer, axes with their basis, modifier terms, provenance and reviewer
resolution. What is approximated or dropped is counted in the report and
listed per annotation in ``report.issues``:

- ``spanless_as_relation``: a document-level annotation is a relation without
  nodes; tools that only read annotations do not see it.
- ``discontinuous_text_joined``: the locations are exact, the annotation text
  is the segment texts joined by a space.
- ``modifiers_as_infon``: modifier terms are one infon, not annotations.
- ``modifier_segments_dropped`` and ``cue_segments_dropped``.
- ``assertion_in_infon``: BioC has no negation or uncertainty; an annotation
  that is not ``present`` looks like a finding to a reader that ignores infons.
- ``review_resolution_in_infon``: the reviewer's ``uncertain`` flag likewise.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from itertools import accumulate
from typing import Any, Literal

from ontospan._interchange import (
    ConversionReport,
    count_dropped_segments,
    document_ids,
    ordered_mentions,
    spanless,
)
from ontospan._version import SCHEMA_VERSION, __version__
from ontospan.models import AnnotatedDocument, Annotation
from ontospan.ontology import OntologyView
from ontospan.refs import annotation_ref

__all__ = ["ConversionReport", "OffsetUnit", "to_bioc_json", "to_bioc_xml"]

OffsetUnit = Literal["codepoint", "utf8"]
KEY = "ontospan.key"
# Characters that XML 1.0 cannot hold, even escaped.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _infons(
    unit: AnnotatedDocument,
    annotation: Annotation,
    ontology: OntologyView | None,
    bundle_id: str | None,
) -> dict[str, str]:
    infons = {"identifier": annotation.term_id, "type": annotation.layer_id}
    label = ontology.label(annotation.term_id) if ontology is not None else None
    if label:
        infons["label"] = label
    for axis in sorted(annotation.axes):
        infons[axis] = annotation.axes[axis].value
        infons[f"{axis}_basis"] = annotation.axes[axis].basis
    if annotation.modifiers:
        infons["modifiers"] = " ".join(m.term_id for m in annotation.modifiers)
    provenance = annotation.provenance
    infons["annotator"] = provenance.actor.actor_id
    infons["annotator_kind"] = provenance.actor.kind
    infons["method"] = provenance.method
    if provenance.source_refs:
        infons["source_refs"] = " ".join(provenance.source_refs)
    for number, note in enumerate(provenance.notes, start=1):
        infons[f"note_{number}"] = note
    resolution = annotation.review_resolution
    if resolution is not None:
        infons["review_outcome"] = resolution.outcome
        infons["reviewer"] = resolution.reviewer.actor_id
        infons["reviewer_kind"] = resolution.reviewer.kind
        if resolution.note is not None:
            infons["review_note"] = resolution.note
    set_id = unit.annotation_set.annotation_set_id
    infons["annotation_ref"] = annotation_ref(
        set_id, annotation.annotation_id, bundle_id
    )
    return infons


def _positions(text: str, offset_unit: OffsetUnit) -> Sequence[int]:
    """The offset in the output unit of every codepoint offset of the text."""
    if offset_unit == "codepoint":
        return range(len(text) + 1)
    if offset_unit == "utf8":
        sizes = (len(char.encode("utf-8")) for char in text)
        return list(accumulate(sizes, initial=0))
    raise ValueError(f"unknown offset unit {offset_unit!r}")


def _note_losses(
    annotation: Annotation, document_id: str, ref: str, report: ConversionReport
) -> None:
    """Count and list what BioC holds only approximately for one annotation."""

    def note(code: str, message: str) -> None:
        report.note(code, ref, f"{document_id}: {message}")

    if not annotation.mentions:
        note("spanless_as_relation", "no text span; written as a relation")
    for index, mention in enumerate(annotation.mentions, start=1):
        if len(mention.segments) > 1:
            message = f"mention {index} is discontinuous; its text is joined"
            note("discontinuous_text_joined", message)
    cues = sum(len(mention.cue_segments) for mention in annotation.mentions)
    if cues:
        note("cue_segments_dropped", f"{cues} cue segment(s) not written")
    if annotation.modifiers:
        report.modifiers_flattened += 1
        note("modifiers_as_infon", "modifier terms written as one infon")
    spans = sum(len(modifier.segments) for modifier in annotation.modifiers)
    if spans:
        note("modifier_segments_dropped", f"{spans} modifier segment(s) not written")
    assertion = annotation.axes.get("assertion")
    if assertion is not None and assertion.value != "present":
        report.assertion_only_in_infons += 1
        note("assertion_in_infon", f"assertion {assertion.value} is only an infon")
    if annotation.review_resolution is not None:
        report.review_resolutions += 1
        outcome = annotation.review_resolution.outcome
        message = f"reviewer resolution {outcome} is only an infon"
        note("review_resolution_in_infon", message)


def _document(
    unit: AnnotatedDocument,
    document_id: str,
    ontology: OntologyView | None,
    bundle_id: str | None,
    offset_unit: OffsetUnit,
    report: ConversionReport,
) -> dict[str, Any]:
    document, annotation_set = unit.document, unit.annotation_set
    infons = {
        "document_id": document.document_id,
        "annotation_set_id": annotation_set.annotation_set_id,
        "set_kind": annotation_set.set_kind,
        "language": document.language,
        "text_sha256": document.text_sha256,
    }
    if document.subject_id is not None:
        infons["subject_id"] = document.subject_id
    if document.case_id is not None:
        infons["case_id"] = document.case_id
    for layer in sorted(annotation_set.layers, key=lambda layer: layer.layer_id):
        infons[f"layer.{layer.layer_id}.profile"] = layer.profile
        infons[f"layer.{layer.layer_id}.ontology"] = layer.ontology.id
        infons[f"layer.{layer.layer_id}.version"] = layer.ontology.version

    annotations = annotation_set.annotations
    for annotation in annotations:
        count_dropped_segments(annotation, report)
    position = _positions(document.text, offset_unit)
    bioc_annotations = []
    for annotation, index, mention in ordered_mentions(annotations):
        annotation_id = annotation.annotation_id
        if len(annotation.mentions) > 1:
            annotation_id = f"{annotation_id}/{index + 1}"
        item_infons = _infons(unit, annotation, ontology, bundle_id)
        item_infons["mention"] = str(index + 1)
        if index == 0:
            ref = item_infons["annotation_ref"]
            _note_losses(annotation, document_id, ref, report)
        bioc_annotations.append(
            {
                "id": annotation_id,
                "infons": item_infons,
                "text": " ".join(seg.text for seg in mention.segments),
                "locations": [
                    {
                        "offset": position[seg.start],
                        "length": position[seg.end] - position[seg.start],
                    }
                    for seg in mention.segments
                ],
            }
        )
        report.mentions += 1
        report.discontinuous += len(mention.segments) > 1
    relations = []
    for annotation in spanless(annotations):
        item_infons = _infons(unit, annotation, ontology, bundle_id)
        item_infons["document_level"] = "true"
        _note_losses(annotation, document_id, item_infons["annotation_ref"], report)
        relations.append(
            {"id": annotation.annotation_id, "infons": item_infons, "nodes": []}
        )
        report.spanless += 1
    report.exported += len(annotations)
    ids = [item["id"] for item in bioc_annotations + relations]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate BioC ids in document {document_id!r}")
    return {
        "id": document_id,
        "infons": infons,
        "passages": [
            {
                "offset": 0,
                "infons": {"type": "text"},
                "text": document.text,
                "sentences": [],
                "annotations": bioc_annotations,
                "relations": [],
            }
        ],
        "annotations": [],
        "relations": relations,
    }


def _date(units: Sequence[AnnotatedDocument]) -> str:
    # The newest set, so the output does not depend on the clock.
    return max(unit.annotation_set.created_at for unit in units).date().isoformat()


def to_bioc_json(
    units: Sequence[AnnotatedDocument],
    ontology: OntologyView | None = None,
    *,
    bundle_id: str | None = None,
    offset_unit: OffsetUnit = "codepoint",
) -> tuple[dict[str, Any], ConversionReport]:
    """A BioC JSON collection (as a dict) and what the export left out.

    ``ontology`` only adds labels. ``bundle_id`` goes into ``annotation_ref``.
    ``offset_unit`` is the unit of annotation offsets and lengths:
    ``codepoint`` (default) or ``utf8`` (bytes, as ``BioC.dtd`` recommends).
    The units are exported as given: select the sets to publish first, for
    example the adjudicated or release set of each document.
    Alternative sets of one document get the id
    ``urn:ontospan:document:<document_id>:<annotation_set_id>``.
    Raises ValueError without units or with ids BioC cannot tell apart.
    """
    if not units:
        raise ValueError("a BioC collection needs at least one document")
    report = ConversionReport()
    documents = [
        _document(unit, document_id, ontology, bundle_id, offset_unit, report)
        for unit, document_id in zip(units, document_ids(units), strict=True)
    ]
    collection = {
        "source": "ontospan",
        "date": _date(units),
        "key": KEY,
        "infons": {
            "offset_unit": offset_unit,
            "ontospan_version": __version__,
            "schema_version": SCHEMA_VERSION,
        },
        "documents": documents,
    }
    return collection, report


def _infon_elements(parent: ET.Element, infons: dict[str, str]) -> None:
    for key, value in infons.items():
        ET.SubElement(parent, "infon", key=key).text = value


def _text(value: str) -> str:
    if _NOT_XML.search(value):
        raise ValueError("text contains characters that XML 1.0 cannot hold")
    return value


def _xml(collection: dict[str, Any]) -> ET.Element:
    root = ET.Element("collection")
    for name in ("source", "date", "key"):
        ET.SubElement(root, name).text = collection[name]
    _infon_elements(root, collection["infons"])
    for document in collection["documents"]:
        element = ET.SubElement(root, "document")
        ET.SubElement(element, "id").text = document["id"]
        _infon_elements(element, document["infons"])
        for passage in document["passages"]:
            node = ET.SubElement(element, "passage")
            _infon_elements(node, passage["infons"])
            ET.SubElement(node, "offset").text = str(passage["offset"])
            ET.SubElement(node, "text").text = _text(passage["text"])
            for annotation in passage["annotations"]:
                item = ET.SubElement(node, "annotation", id=annotation["id"])
                _infon_elements(item, annotation["infons"])
                for location in annotation["locations"]:
                    ET.SubElement(
                        item,
                        "location",
                        offset=str(location["offset"]),
                        length=str(location["length"]),
                    )
                ET.SubElement(item, "text").text = annotation["text"]
        for relation in document["relations"]:
            item = ET.SubElement(element, "relation", id=relation["id"])
            _infon_elements(item, relation["infons"])
    for element in root.iter():
        for value in element.attrib.values():
            _text(value)
        if element.text is not None:
            _text(element.text)
    return root


def to_bioc_xml(
    units: Sequence[AnnotatedDocument],
    ontology: OntologyView | None = None,
    *,
    bundle_id: str | None = None,
    offset_unit: OffsetUnit = "codepoint",
) -> tuple[str, ConversionReport]:
    """A BioC XML collection (``BioC.dtd``) and what the export left out.

    The same content as ``to_bioc_json``. Also raises ValueError for text
    with characters that XML 1.0 cannot hold (most C0 controls).
    """
    collection, report = to_bioc_json(
        units, ontology, bundle_id=bundle_id, offset_unit=offset_unit
    )
    root = _xml(collection)
    ET.indent(root)
    body = ET.tostring(root, encoding="unicode")
    header = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE collection SYSTEM "BioC.dtd">\n'
    )
    return header + body + "\n", report
