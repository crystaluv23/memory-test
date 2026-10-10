"""Explicit, conservative legacy migration preview; never fabricates provenance."""
import copy

from .common import digest
from .contracts import (bounded_text, canonical, exact_object, identifier, references,
                        require, sha256, timestamp)


def adapt_leaf(snapshot, record_id, version, source_mapping, evidence, ns, cutoff, at, outcome):
    """Return original + typed proposal. Caller retains this artifact before ingest.

    Missing metadata is a refusal, not a default. A caller must migrate revisions
    in order; CanonicalCards still checks expected_version and active supersedes.
    """
    canonical(snapshot)
    exact_object(snapshot, {"adapter", "records", "sources"}, "invalid_legacy_snapshot")
    require(snapshot["adapter"] == "reference-v1", "unsupported_legacy_adapter")
    require(type(snapshot["records"]) is list and type(snapshot["sources"]) is dict, "invalid_legacy_snapshot")
    require(type(version) is int and version > 0, "invalid_legacy_version")
    timestamp(at)
    require(outcome in ("success", "failure", "unknown"), "invalid_legacy_outcome")
    matches = [r for r in snapshot["records"] if type(r) is dict and r.get("id") == record_id and r.get("version") == version]
    require(len(matches) == 1, "unresolved_legacy_record")
    raw = matches[0]
    required = {"id", "version", "category", "level", "brief", "content", "conditions", "limits", "source_refs", "support_ids", "links", "supersedes", "support_versions", "episode_id", "sequence", "expected_version"}
    exact_object(raw, required, "invalid_legacy_fields")
    require(type(raw["level"]) is int and raw["level"] == 0 and raw["support_ids"] == [] and raw["links"] == [] and raw["support_versions"] == {}, "legacy_abstraction_not_canonical")
    require(raw["supersedes"] == [], "legacy_supersedes_requires_explicit_mapping")
    require(raw["category"] in evidence.kernel["taxonomy"], "unregistered_type_hint")
    require(type(raw["source_refs"]) is list and bool(raw["source_refs"]), "missing_legacy_source")
    require(type(source_mapping) is dict and set(source_mapping) == {r.get("id") for r in raw["source_refs"]}, "incomplete_legacy_source_mapping")
    mapped = []
    for ref in raw["source_refs"]:
        exact_object(ref, {"id", "sha256"}, "invalid_legacy_source_ref")
        source = snapshot["sources"].get(ref["id"])
        require(type(source) is dict and set(source) == {"text", "sha256"}, "unresolved_legacy_source")
        require(source["sha256"] == ref["sha256"] == digest(source["text"]), "legacy_source_hash_mismatch")
        new_ref = source_mapping[ref["id"]]
        new_source = evidence.read(new_ref, "extractor", ns, cutoff)
        require(new_source["text"] == source["text"], "legacy_source_text_mismatch")
        require(timestamp(new_source["timestamp"]) <= timestamp(at), "future_source_timestamp")
        mapped.append(new_ref)
    references(mapped, {"source"}, evidence.kernel["limits"]["card_evidence_refs"], 1)
    for key in ("brief", "content", "conditions", "limits"):
        bounded_text(raw[key], 16000, "invalid_legacy_text")
    detail = raw["content"] + "\n\nApplicability:\n" + raw["conditions"] + "\n\nExceptions and limits:\n" + raw["limits"]
    bounded_text(detail, evidence.kernel["limits"]["card_detail_bytes"], "invalid_card_detail")
    bounded_text(raw["brief"], evidence.kernel["limits"]["card_brief_bytes"], "invalid_card_brief")
    id_ = "legacy-" + sha256({"id": record_id, "namespace": ns})
    identifier(id_)
    card = {"id": id_, "expected_version": version-1, "type_hint": raw["category"], "brief": raw["brief"], "detail": detail,
            "evidence_refs": mapped, "outcome": outcome, "status": "active", "supersedes": []}
    return {"schema_version": 1, "adapter": "legacy-leaf-to-canonical-v0", "original_snapshot": copy.deepcopy(snapshot),
            "original_sha256": sha256(snapshot), "record_id": record_id, "record_version": version,
            "timestamp": at, "namespace": ns, "cutoff": cutoff, "proposal": {"cards": [card]},
            "semantic_validation": "requires_independent_review"}
