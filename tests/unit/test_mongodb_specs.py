"""Unit tests: structured MongoDB spec models, normalization, guard, tenant.

Covers task 1.1 (strict JSON-compatible specs), 1.2 (profiles and errors),
2.1 (deterministic normalization/fingerprinting), 2.2 (allowlist
validation), and 2.3 (tenant obligation/routing evidence validation).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from nl2data_core.governance.models import MandatoryFilterObligation
from nl2data_mongodb.config import MongoAdapterConfig, MongoProfile
from nl2data_mongodb.models import (
    MongoOperation,
    MongoQuerySpec,
    MongoUnavailableError,
    TenantObligation,
    mongo_spec_json,
)
from nl2data_mongodb.normalize import (
    assert_json_compatible,
    mql_spec_fingerprint,
    normalize_mql_value,
    predicate_fingerprint,
)
from nl2data_mongodb.validation import MongoGuardPolicy, run_guard


def make_spec(**overrides) -> MongoQuerySpec:
    values = {
        "spec_id": "spec-1",
        "operation": MongoOperation.FIND,
        "collection": "orders",
        "filter": {"region": {"$eq": "emea"}},
        "projection": {"order_id": 1, "amount": 1},
        "sort": {"amount": -1},
        "limit": 10,
    }
    values.update(overrides)
    return MongoQuerySpec(**values)


def make_policy(**overrides) -> MongoGuardPolicy:
    values = {
        "allowed_collections": frozenset({"orders"}),
        "allowed_fields": frozenset(
            {"order_id", "amount", "region", "status", "customer_id", "address.city"}
        ),
        "require_limit": True,
    }
    values.update(overrides)
    return MongoGuardPolicy(**values)


class TestSpecModels:
    def test_spec_is_immutable(self) -> None:
        spec = make_spec()
        with pytest.raises(ValidationError):
            spec.filter = {}  # type: ignore[misc]
        # Copies are isolated: the original is never mutated.
        copied = spec.model_copy(update={"limit": 5})
        assert copied is not spec
        assert copied.limit == 5
        assert spec.limit == 10

    def test_nested_spec_containers_cannot_be_mutated(self) -> None:
        spec = make_spec(
            filter={"region": {"$in": ["emea", "apac"]}},
            pipeline=None,
        )
        with pytest.raises(TypeError):
            spec.filter["region"] = {"$eq": "emea"}  # type: ignore[index]
        with pytest.raises(TypeError):
            spec.filter["region"]["$in"] = ()  # type: ignore[index]
        with pytest.raises(TypeError):
            spec.filter["region"]["$in"][0] = "other"  # type: ignore[index]

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MongoQuerySpec.model_validate(
                {"spec_id": "s1", "operation": "find", "collection": "orders", "extra": 1}
            )

    def test_unsupported_operation_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MongoQuerySpec.model_validate(
                {
                    "spec_id": "s1",
                    "operation": "delete",
                    "collection": "orders",
                    "filter": {},
                }
            )

    def test_aggregate_requires_pipeline(self) -> None:
        with pytest.raises(ValidationError):
            make_spec(operation=MongoOperation.AGGREGATE, pipeline=None)

    def test_only_aggregate_may_carry_pipeline(self) -> None:
        with pytest.raises(ValidationError):
            make_spec(operation=MongoOperation.FIND, pipeline=({"$match": {}},))

    def test_nested_dotted_paths_are_supported(self) -> None:
        spec = make_spec(
            filter={"address.city": {"$eq": "oslo"}},
            projection={"address.city": 1},
        )
        assert spec.filter == {"address.city": {"$eq": "oslo"}}

    def test_non_json_values_are_rejected(self) -> None:
        with pytest.raises(TypeError):
            make_spec(filter={"region": datetime.now(UTC)})

    def test_spec_wire_form_round_trips(self) -> None:
        spec = make_spec()
        payload = mongo_spec_json(spec)
        rebuilt = MongoQuerySpec.model_validate_json(payload)
        assert rebuilt == spec
        assert rebuilt.model_dump() == spec.model_dump()

    def test_count_operation_is_expressible(self) -> None:
        spec = make_spec(operation=MongoOperation.COUNT, filter={"status": {"$eq": 1}})
        assert spec.operation == MongoOperation.COUNT


class TestNormalization:
    def test_normalization_sorts_mapping_keys(self) -> None:
        assert normalize_mql_value({"b": 1, "a": 2}) == {"a": 2, "b": 1}

    def test_normalization_rejects_native_objects(self) -> None:
        with pytest.raises(TypeError):
            normalize_mql_value({"when": datetime.now(UTC)})

    def test_assert_json_compatible_rejects_bytes(self) -> None:
        with pytest.raises(TypeError):
            assert_json_compatible({"data": b"raw"}, path="filter")

    def test_spec_fingerprint_is_order_independent(self) -> None:
        first = mql_spec_fingerprint(
            make_spec(filter={"a": {"$eq": 1}, "b": {"$gt": 2}})
        )
        second = mql_spec_fingerprint(
            make_spec(filter={"b": {"$gt": 2}, "a": {"$eq": 1}})
        )
        assert first == second
        assert first.startswith("sha256:")

    def test_equal_specs_produce_equal_fingerprints(self) -> None:
        assert mql_spec_fingerprint(make_spec()) == mql_spec_fingerprint(make_spec())

    def test_predicate_fingerprint_matches_governance_obligation(self) -> None:
        obligation = MandatoryFilterObligation(
            obligation_id="ob-1", field_id="region", operator="$eq", value="emea"
        )
        assert predicate_fingerprint("region", "$eq", "emea") == obligation.fingerprint

    def test_tenant_obligation_fingerprint_is_computed(self) -> None:
        obligation = TenantObligation(field_id="region", operator="$eq", value="emea")
        assert obligation.fingerprint == predicate_fingerprint("region", "$eq", "emea")
        assert obligation.fingerprint.startswith("sha256:")


class TestGuard:
    def test_allowed_spec_is_accepted(self) -> None:
        result = run_guard(make_spec(), make_policy())
        assert result.accepted
        assert result.reasons == ()
        assert result.fingerprint.startswith("sha256:")

    def test_empty_allowlist_denies_everything(self) -> None:
        policy = make_policy(allowed_collections=frozenset())
        assert run_guard(make_spec(), policy).rejected

    def test_collection_outside_allowlist_is_rejected(self) -> None:
        policy = make_policy(allowed_collections=frozenset({"customers"}))
        result = run_guard(make_spec(), policy)
        assert result.rejected
        assert any("collection" in reason for reason in result.reasons)

    def test_field_outside_allowlist_is_rejected(self) -> None:
        policy = make_policy(allowed_fields=frozenset({"order_id"}))
        result = run_guard(make_spec(), policy)
        assert result.rejected
        assert any("region" in reason for reason in result.reasons)

    def test_nested_path_must_match_exactly(self) -> None:
        policy = make_policy(allowed_fields=frozenset({"address.city"}))
        spec = make_spec(
            filter={"address": {"$eq": "oslo"}},
            projection={"address.city": 1},
            sort={},
        )
        assert run_guard(spec, policy).rejected
        spec = make_spec(
            filter={"address.city": {"$eq": "oslo"}},
            projection={"address.city": 1},
            sort={},
        )
        assert run_guard(spec, policy).accepted

    def test_unapproved_operator_is_rejected(self) -> None:
        policy = make_policy(allowed_operators=frozenset({"$eq"}))
        spec = make_spec(filter={"amount": {"$gt": 100}})
        result = run_guard(spec, policy)
        assert result.rejected
        assert any("operator" in reason for reason in result.reasons)

    def test_js_constructs_are_rejected(self) -> None:
        for js_filter in (
            {"$where": "this.amount > 100"},
            {"amount": {"$regex": ".*"}},
            {"$expr": {"$gt": ["$a", 1]}},
            {"$function": {"body": "return 1"}},
            {"$accumulator": {"init": "x"}},
        ):
            result = run_guard(make_spec(filter=js_filter), make_policy())
            assert result.rejected, js_filter
            assert any(
                any(
                    key in reason
                    for key in ("$where", "$regex", "$expr", "$function", "$accumulator")
                )
                for reason in result.reasons
            ), (js_filter, result.reasons)

    def test_projection_mixing_is_rejected(self) -> None:
        spec = make_spec(projection={"order_id": 1, "amount": 0})
        assert run_guard(spec, make_policy()).rejected

    def test_projection_expression_values_are_rejected_for_find(self) -> None:
        spec = make_spec(projection={"order_id": "$amount"})
        assert run_guard(spec, make_policy()).rejected

    def test_wildcard_projection_is_rejected(self) -> None:
        for marker in ("**", "$**", "*", "order.$"):
            spec = make_spec(projection={marker: 1})
            assert run_guard(spec, make_policy()).rejected, marker

    def test_invalid_sort_direction_is_rejected(self) -> None:
        spec = make_spec(sort={"amount": 0})
        assert run_guard(spec, make_policy()).rejected

    def test_unbounded_find_is_rejected_when_limit_required(self) -> None:
        spec = make_spec(limit=None)
        result = run_guard(spec, make_policy())
        assert result.rejected
        assert any("no limit" in reason for reason in result.reasons)

    def test_find_without_projection_is_rejected(self) -> None:
        result = run_guard(make_spec(projection={}), make_policy())
        assert result.rejected
        assert any("explicit projection" in reason for reason in result.reasons)

    def test_limit_and_skip_bounds_are_enforced(self) -> None:
        policy = make_policy(max_limit=100, max_skip=10)
        assert run_guard(make_spec(limit=101), policy).rejected
        assert run_guard(make_spec(skip=11), policy).rejected
        assert run_guard(make_spec(limit=100, skip=10), policy).accepted

    def test_forbidden_stages_are_rejected(self) -> None:
        for stage in ("$lookup", "$out", "$merge", "$facet", "$search", "$unionWith"):
            spec = make_spec(
                operation=MongoOperation.AGGREGATE,
                pipeline=({stage: {}}, {"$limit": 5}),
            )
            result = run_guard(spec, make_policy())
            assert result.rejected, stage
            assert any("not allowed" in reason for reason in result.reasons)

    def test_unapproved_stage_is_rejected(self) -> None:
        policy = make_policy(allowed_stages=frozenset({"$match", "$limit"}))
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            pipeline=({"$group": {"_id": None, "n": {"$sum": 1}}}, {"$limit": 5}),
        )
        result = run_guard(spec, policy)
        assert result.rejected
        assert any("not approved" in reason for reason in result.reasons)

    def test_unbounded_aggregate_is_rejected(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            filter={},
            projection={},
            sort={},
            limit=None,
            pipeline=({"$match": {"region": {"$eq": "emea"}}},),
        )
        result = run_guard(spec, make_policy())
        assert result.rejected
        assert any("unbounded" in reason for reason in result.reasons)

    def test_aggregate_with_terminal_limit_is_bounded(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            filter={},
            projection={},
            sort={},
            limit=None,
            pipeline=(
                {"$match": {"region": {"$eq": "emea"}}},
                {"$project": {"order_id": 1}},
                {"$limit": 5},
            ),
        )
        assert run_guard(spec, make_policy()).accepted

    def test_aggregate_limit_must_bound_the_final_output(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            limit=None,
            pipeline=(
                {"$limit": 1},
                {"$unwind": "$customer_id"},
                {"$project": {"customer_id": 1}},
            ),
        )
        result = run_guard(spec, make_policy())
        assert result.rejected
        assert any("unbounded" in reason for reason in result.reasons)

    def test_aggregate_without_a_result_shape_is_rejected(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            projection={},
            pipeline=({"$match": {"region": {"$eq": "emea"}}}, {"$limit": 5}),
        )
        result = run_guard(spec, make_policy())
        assert result.rejected
        assert any("constrain output fields" in reason for reason in result.reasons)

    def test_aggregate_rejects_unused_top_level_execution_fields(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            filter={"region": {"$eq": "emea"}},
            projection={"order_id": 1},
            sort={},
            pipeline=(
                {"$project": {"order_id": 1}},
                {"$limit": 5},
            ),
        )
        result = run_guard(spec, make_policy())
        assert result.rejected
        assert any("must be expressed in the pipeline" in reason for reason in result.reasons)

    def test_group_expression_scope_is_enforced(self) -> None:
        policy = make_policy(allowed_expressions=frozenset({"$sum"}))
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            pipeline=(
                {"$group": {"_id": "$status", "total": {"$avg": "$amount"}}},
                {"$limit": 5},
            ),
        )
        result = run_guard(spec, policy)
        assert result.rejected
        assert any("not approved" in reason for reason in result.reasons)

    def test_sum_constant_must_be_one(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            pipeline=(
                {"$group": {"_id": None, "n": {"$sum": 7}}},
                {"$limit": 5},
            ),
        )
        result = run_guard(spec, make_policy())
        assert result.rejected
        assert any("counting" in reason for reason in result.reasons)

    def test_group_id_must_be_null_or_path(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            pipeline=(
                {"$group": {"_id": {"status": "$status"}, "n": {"$sum": 1}}},
                {"$limit": 5},
            ),
        )
        assert run_guard(spec, make_policy()).rejected

    def test_aggregate_project_rename_expression_is_bounded(self) -> None:
        policy = make_policy(allowed_fields=frozenset({"status", "amount"}))
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            collection="orders",
            filter={},
            projection={},
            sort={},
            pipeline=(
                {"$group": {"_id": "$status", "total": {"$sum": "$amount"}}},
                {"$project": {"status": "$_id", "total": 1, "_id": 0}},
                {"$limit": 5},
            ),
        )
        result = run_guard(spec, policy)
        assert result.accepted, result.reasons

    def test_aggregate_project_wildcard_rename_is_rejected(self) -> None:
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            pipeline=(
                {"$group": {"_id": None, "n": {"$sum": 1}}},
                {"$project": {"bad.path": "$_id", "n": 1, "_id": 0}},
                {"$limit": 5},
            ),
        )
        assert run_guard(spec, make_policy()).rejected

    def test_guard_fingerprint_is_stable(self) -> None:
        policy = make_policy()
        first = run_guard(make_spec(), policy)
        second = run_guard(make_spec(), policy)
        assert first.fingerprint == second.fingerprint


class TestTenantValidation:
    def make_spec_with_obligation(self, *, obligation: TenantObligation | None) -> MongoQuerySpec:
        return make_spec(tenant_obligation=obligation)

    def test_pooled_requires_obligation(self) -> None:
        policy = make_policy(tenant_profile="pooled")
        spec = make_spec()
        assert run_guard(spec, policy).rejected

    def test_pooled_obligation_fingerprint_must_match(self) -> None:
        required = predicate_fingerprint("region", "$eq", "emea")
        policy = make_policy(
            tenant_profile="pooled", required_obligation_fingerprint=required
        )
        spec = make_spec(
            tenant_obligation=TenantObligation(field_id="region", operator="$eq", value="emea")
        )
        assert run_guard(spec, policy).accepted
        wrong = make_spec(
            tenant_obligation=TenantObligation(field_id="region", operator="$eq", value="apac")
        )
        result = run_guard(wrong, policy)
        assert result.rejected
        assert any("does not match" in reason for reason in result.reasons)

    def test_pooled_obligation_must_be_enforced_by_the_filter(self) -> None:
        policy = make_policy(
            tenant_profile="pooled",
            required_obligation_fingerprint=predicate_fingerprint("region", "$eq", "emea"),
        )
        spec = make_spec(
            filter={"region": {"$eq": "apac"}},
            tenant_obligation=TenantObligation(field_id="region", operator="$eq", value="emea"),
        )
        result = run_guard(spec, policy)
        assert result.rejected
        assert any("not enforced" in reason for reason in result.reasons)

    def test_pooled_aggregate_obligation_must_be_enforced_by_match(self) -> None:
        policy = make_policy(
            tenant_profile="pooled",
            required_obligation_fingerprint=predicate_fingerprint("region", "$eq", "emea"),
        )
        spec = make_spec(
            operation=MongoOperation.AGGREGATE,
            filter={},
            projection={},
            sort={},
            tenant_obligation=TenantObligation(field_id="region", operator="$eq", value="emea"),
            pipeline=(
                {"$match": {"region": {"$eq": "emea"}}},
                {"$project": {"order_id": 1}},
                {"$limit": 5},
            ),
        )
        assert run_guard(spec, policy).accepted

    def test_pooled_obligation_field_must_be_allowed(self) -> None:
        policy = make_policy(
            tenant_profile="pooled",
            allowed_fields=frozenset({"order_id", "amount"}),
            required_obligation_fingerprint=predicate_fingerprint("region", "$eq", "emea"),
        )
        spec = make_spec(
            tenant_obligation=TenantObligation(field_id="region", operator="$eq", value="emea")
        )
        assert run_guard(spec, policy).rejected

    def test_isolated_profile_requires_matching_routing_evidence(self) -> None:
        from nl2data_mongodb.models import RoutingEvidence, RoutingKind

        policy = make_policy(tenant_profile="schema_isolated")
        assert run_guard(make_spec(), policy).rejected
        wrong_kind = make_spec(
            routing_evidence=RoutingEvidence(kind=RoutingKind.DATABASE, reference="sales")
        )
        result = run_guard(wrong_kind, policy)
        assert result.rejected
        assert any("does not match" in reason for reason in result.reasons)
        matching = make_spec(
            routing_evidence=RoutingEvidence(kind=RoutingKind.SCHEMA, reference="sales")
        )
        assert run_guard(matching, policy).accepted

    def test_database_and_deployment_profiles(self) -> None:
        from nl2data_mongodb.models import RoutingEvidence, RoutingKind

        database_policy = make_policy(tenant_profile="database_isolated")
        deployment_policy = make_policy(tenant_profile="deployment_isolated")
        assert run_guard(
            make_spec(
                routing_evidence=RoutingEvidence(kind=RoutingKind.DATABASE, reference="db1")
            ),
            database_policy,
        ).accepted
        assert run_guard(
            make_spec(
                routing_evidence=RoutingEvidence(
                    kind=RoutingKind.DEPLOYMENT, reference="cluster1"
                )
            ),
            deployment_policy,
        ).accepted


class TestAdapterConfig:
    def test_config_defaults_to_fake_profile(self) -> None:
        config = MongoAdapterConfig()
        assert config.profile == MongoProfile.FAKE
        assert config.require_limit is True
        assert config.max_rows == 100_000

    def test_config_requires_valid_fingerprint(self) -> None:
        with pytest.raises(ValidationError):
            MongoAdapterConfig(snapshot_fingerprint="not-a-fingerprint")

    def test_unavailable_error_is_safe(self) -> None:
        error = MongoUnavailableError(
            "the mongodb driver or service is unavailable",
            details={"cause_type": "Unavailable"},
        )
        assert error.code.value == "MONGO_UNAVAILABLE"
        assert error.retryable is False
