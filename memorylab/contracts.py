"""Strict V0 contracts. Nothing in a model proposal grants controller authority."""
import copy
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,95}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
NAMESPACE = re.compile(r"(?:control|task/[A-Za-z][A-Za-z0-9_-]{0,95}|research/(?:evaluator|proposer))\Z")
REF_KEYS = {"kind", "id", "version", "sha256", "namespace"}


class ContractError(ValueError):
    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(code + (": " + detail if detail else ""))


def require(condition, code, detail=""):
    if not condition:
        raise ContractError(code, detail)


def strict_json(value):
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        require(math.isfinite(value), "nonfinite_json")
    elif type(value) is list:
        for item in value:
            strict_json(item)
    elif type(value) is dict:
        require(all(type(k) is str for k in value), "invalid_json_key")
        for item in value.values():
            strict_json(item)
    else:
        raise ContractError("invalid_json_type", type(value).__name__)


def canonical(value):
    try:
        strict_json(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, RecursionError) as exc:
        raise ContractError("invalid_json_encoding") from exc


def sha256(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def text_hash(text):
    require(type(text) is str, "invalid_text")
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError as exc:
        raise ContractError("invalid_text_encoding") from exc


def exact_object(value, keys, code="invalid_fields"):
    require(type(value) is dict, "expected_object")
    require(set(value) == set(keys), code,
            "expected " + ",".join(sorted(keys)))
    canonical(value)


def positive_int(value, code="invalid_version"):
    require(type(value) is int and value > 0, code)


def identifier(value):
    require(type(value) is str and ID.fullmatch(value) is not None, "invalid_id")


def namespace(value):
    require(type(value) is str and NAMESPACE.fullmatch(value) is not None, "invalid_namespace")


def bounded_text(value, maximum, code):
    require(type(value) is str and bool(value.strip()), code)
    try:
        require(len(value.encode("utf-8")) <= maximum, code)
    except UnicodeError as exc:
        raise ContractError("invalid_text_encoding") from exc


def timestamp(value):
    require(type(value) is str, "invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid_timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "naive_timestamp")
    return parsed


def reference(value, kinds=None):
    exact_object(value, REF_KEYS, "invalid_reference_fields")
    identifier(value["id"])
    identifier(value["kind"])
    positive_int(value["version"])
    namespace(value["namespace"])
    require(type(value["sha256"]) is str and SHA.fullmatch(value["sha256"]) is not None,
            "invalid_reference_hash")
    require(kinds is None or value["kind"] in kinds, "invalid_reference_kind")
    return value


def references(values, kinds, maximum, minimum=0):
    require(type(values) is list and minimum <= len(values) <= maximum, "invalid_reference_count")
    for ref in values:
        reference(ref, kinds)
    require(len({canonical(r) for r in values}) == len(values), "duplicate_reference")


def enum(value, choices, code="unregistered_value"):
    # Python equality would accept True as 1 and 1.0 as 1.
    require(any(type(value) is type(c) and value == c for c in choices), code)


def read_kernel(path=None):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key", key)
            result[key] = value
        return result
    value = json.loads(Path(path or ROOT / "configs/kernel_v0.json").read_text(),
                       object_pairs_hook=unique,
                       parse_constant=lambda x: (_ for _ in ()).throw(ContractError("nonfinite_json", x)))
    return validate_kernel(value)


def validate_kernel(value):
    exact_object(value, {"schema_version", "status", "live_execution_authorized", "memory_fields",
        "memory_initial", "scenario_families", "scenario_fields", "scenario_initial", "verifier_mapping",
        "taxonomy", "source_event_types", "role_source_namespaces", "limits", "extractor",
        "research_hypothesis_scope"}, "invalid_kernel_fields")
    require(value["schema_version"] == "kernel-contract-v0", "unsupported_kernel_version")
    require(value["live_execution_authorized"] is False, "kernel_cannot_authorize_live")
    # Freeze the supported search space in code as well as configuration: a proposer
    # cannot turn an arbitrary field into a registered primitive by editing JSON.
    expected_memory = {"organizer.type", "consolidation.type", "retrieval.type", "loader.global_briefs",
        "loader.local_briefs", "credit.type", "reflection.trigger", "invalidation.type"}
    expected_scenario = {"distractor_count", "transfer_distance", "rule_change", "context_profile", "semantic_interference"}
    exact_object(value["memory_fields"], expected_memory, "invalid_memory_registry")
    exact_object(value["scenario_fields"], expected_scenario, "invalid_scenario_registry")
    primitives = {
        "organizer.type": ["flat", "fixed_tree"], "consolidation.type": ["none", "single_index"],
        "retrieval.type": ["lexical", "tree", "hybrid_tree_lexical"], "credit.type": ["none", "outcome_ema"],
        "reflection.trigger": ["off", "failure", "feedback"], "invalidation.type": ["none", "explicit_supersession"]}
    scenario_primitives = {"transfer_distance": ["near", "far"], "rule_change": ["none", "registered_revision"],
        "context_profile": ["clean", "pressure"], "semantic_interference": ["none", "similar_inapplicable"]}
    for field, choices in primitives.items():
        require(canonical(value["memory_fields"][field]) == canonical(choices), "unsupported_primitive_registry")
    for field, choices in scenario_primitives.items():
        require(canonical(value["scenario_fields"][field]) == canonical(choices), "unsupported_primitive_registry")
    for fields in (value["memory_fields"], value["scenario_fields"]):
        for choices in fields.values():
            require(type(choices) is list and bool(choices), "empty_registry")
            require(all(type(c) in (str, int) for c in choices), "invalid_registry_value")
            require(len({canonical(c) for c in choices}) == len(choices), "duplicate_registry_value")
    for field in ("loader.global_briefs", "loader.local_briefs"):
        require(all(type(c) is int and 0 <= c <= 16 for c in value["memory_fields"][field]), "invalid_loader_registry")
    require(all(type(c) is int and 0 <= c <= 8 for c in value["scenario_fields"]["distractor_count"]), "invalid_distractor_registry")
    require(type(value["scenario_families"]) is list and bool(value["scenario_families"]), "invalid_families")
    for family in value["scenario_families"]:
        identifier(family)
    require(len(set(value["scenario_families"])) == len(value["scenario_families"]), "duplicate_family")
    families = ["feedback_retention", "workflow_reuse", "environment_gotcha", "stale_memory", "abstraction_transfer", "context_interference"]
    require(value["scenario_families"] == families, "unsupported_family_registry")
    exact_object(value["verifier_mapping"], value["scenario_families"], "invalid_verifier_mapping")
    require(value["verifier_mapping"] == {f: f + "-v0" for f in families}, "unsupported_verifier_registry")
    expected_limits = {"proposal_hypothesis_bytes", "proposal_evidence_refs", "source_text_bytes",
        "extract_cards_per_batch", "card_brief_bytes", "card_detail_bytes", "card_evidence_refs", "card_supersedes_refs"}
    exact_object(value["limits"], expected_limits, "invalid_limits")
    for limit in value["limits"].values():
        positive_int(limit, "invalid_limit")
    require(type(value["taxonomy"]) is list and bool(value["taxonomy"]), "invalid_taxonomy")
    for path_value in value["taxonomy"]:
        require(type(path_value) is str and re.fullmatch(r"[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*", path_value), "invalid_taxonomy")
        parts = path_value.split("/")
        require(all("/".join(parts[:i]) in value["taxonomy"] for i in range(1, len(parts))), "missing_taxonomy_parent")
    require(len(set(value["taxonomy"])) == len(value["taxonomy"]), "duplicate_taxonomy")
    require(value["source_event_types"] == ["user_input", "agent_output", "tool_call", "tool_result", "file_diff", "explicit_feedback", "visible_outcome", "explicit_hypothesis"], "unsupported_event_registry")
    exact_object(value["role_source_namespaces"], {"solver", "extractor", "reflector", "evaluator", "proposer"}, "invalid_roles")
    for patterns in value["role_source_namespaces"].values():
        require(type(patterns) is list and all(p in ("task/*", "research/evaluator", "research/proposer") for p in patterns), "invalid_role_namespace")
    require(value["role_source_namespaces"]["solver"] == [], "solver_source_access")
    require(value["role_source_namespaces"]["evaluator"] == ["research/evaluator"] and value["role_source_namespaces"]["proposer"] == ["research/proposer"], "research_namespace_leak")
    exact_object(value["extractor"], {"schema_version", "id", "model", "prompt", "live_ready"}, "invalid_extractor")
    require(type(value["extractor"]["schema_version"]) is int and value["extractor"]["schema_version"] == 1, "invalid_extractor")
    identifier(value["extractor"]["id"])
    bounded_text(value["extractor"]["prompt"], 16000, "invalid_extractor_prompt")
    require(type(value["extractor"]["live_ready"]) is bool, "invalid_extractor_ready")
    require(value["extractor"]["model"] is None or (type(value["extractor"]["model"]) is str and bool(value["extractor"]["model"].strip())), "invalid_extractor_model")
    require(not value["extractor"]["live_ready"] or value["extractor"]["model"] is not None, "missing_extractor_model")
    validate_config("memory", value["memory_initial"], value)
    validate_config("scenario", value["scenario_initial"], value)
    return copy.deepcopy(value)


def validate_config(kind, config, kernel):
    require(kind in ("memory", "scenario"), "invalid_spec_kind")
    fields = kernel[kind + "_fields"]
    exact_object(config, set(fields) | ({"family"} if kind == "scenario" else set()), "invalid_config_fields")
    for field, choices in fields.items():
        enum(config[field], choices)
    if kind == "scenario":
        enum(config["family"], kernel["scenario_families"], "unregistered_family")
    elif config["retrieval.type"] in ("tree", "hybrid_tree_lexical"):
        require(config["organizer.type"] == "fixed_tree", "incompatible_components", "tree retrieval needs fixed_tree")


def validate_spec(spec, kernel):
    exact_object(spec, {"schema_version", "kind", "id", "version", "parent", "config"}, "invalid_spec_fields")
    require(type(spec["schema_version"]) is int and spec["schema_version"] == 1, "unsupported_spec_schema")
    identifier(spec["id"])
    require(type(spec["version"]) is int and spec["version"] == 1, "invalid_spec_version")
    validate_config(spec["kind"], spec["config"], kernel)
    if spec["parent"] is not None:
        reference(spec["parent"], {spec["kind"]})
        require(spec["parent"]["namespace"] == "control", "invalid_spec_namespace")
        require(spec["parent"]["id"] != spec["id"], "self_parent")
    return spec


def mutation_candidate(proposal, parent, parent_ref, kernel):
    exact_object(proposal, {"schema_version", "kind", "parent", "mutation", "hypothesis", "evidence_refs"}, "invalid_proposal_fields")
    require(type(proposal["schema_version"]) is int and proposal["schema_version"] == 1, "unsupported_proposal_schema")
    require(proposal["kind"] == parent["kind"], "proposal_kind_mismatch")
    validate_spec(parent, kernel)
    reference(parent_ref, {parent["kind"]})
    reference(proposal["parent"], {parent["kind"]})
    require(canonical(proposal["parent"]) == canonical(parent_ref) and sha256(parent) == parent_ref["sha256"], "stale_parent")
    bounded_text(proposal["hypothesis"], kernel["limits"]["proposal_hypothesis_bytes"], "invalid_hypothesis")
    references(proposal["evidence_refs"], {"source", "card", "event"}, kernel["limits"]["proposal_evidence_refs"])
    if proposal["mutation"] is None:
        return None
    mutation = proposal["mutation"]
    exact_object(mutation, {"field", "old", "new"}, "invalid_mutation_fields")
    fields = kernel[parent["kind"] + "_fields"]
    require(type(mutation["field"]) is str and mutation["field"] in fields, "unregistered_field")
    field = mutation["field"]
    require(canonical(mutation["old"]) == canonical(parent["config"][field]), "stale_field")
    enum(mutation["new"], fields[field])
    if canonical(mutation["new"]) == canonical(mutation["old"]):
        return None
    config = copy.deepcopy(parent["config"])
    config[field] = copy.deepcopy(mutation["new"])
    validate_config(parent["kind"], config, kernel)
    identity = sha256({"parent": parent_ref, "config": config})
    child = {"schema_version": 1, "kind": parent["kind"], "id": parent["kind"] + "-" + identity,
             "version": 1, "parent": copy.deepcopy(parent_ref), "config": config}
    return validate_spec(child, kernel)
