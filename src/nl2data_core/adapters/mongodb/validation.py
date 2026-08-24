"""Allowlist-driven validation of structured MQL specifications.

Validation is spec/AST based: collections, dotted paths, operators,
pipeline stages, expressions, projections, and result limits are checked
from typed structures.  Unknown operators/stages, wildcard projections,
JavaScript constructs, and unbounded operations fail closed.  Tenant
obligations and routing evidence are verified before any driver call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nl2data_core.canonical import sha256_fingerprint

from .models import (
    MongoAdapterError,
    MongoGuardResult,
    MongoOperation,
    MongoQuerySpec,
)

#: Leaf comparison operators an allowed filter value may carry.
DEFAULT_OPERATORS = frozenset({"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin"})

#: Aggregation pipeline stages with a read-only, bounded profile.
DEFAULT_STAGES = frozenset(
    {"$match", "$project", "$sort", "$skip", "$limit", "$group", "$count", "$unwind"}
)

#: Expression operators allowed inside ``$group`` accumulators.
DEFAULT_EXPRESSIONS = frozenset({"$sum", "$avg", "$min", "$max"})

#: Keys that would inject JavaScript, regex evaluation, or unapproved
#: constructs into a filter; each is rejected with a specific reason.
_JS_KEYS = frozenset(
    {
        "$where",
        "$expr",
        "$function",
        "$accumulator",
        "$jsScope",
        "$regex",
        "$options",
        "$text",
        "$search",
        "$near",
        "$nearSphere",
        "$geoWithin",
        "$geoIntersects",
        "$elemMatch",
        "$all",
        "$type",
        "$mod",
        "$not",
        "$nor",
        "$and",
        "$or",
        "$comment",
        "$natural",
        "$hint",
        "$max",
        "$min",
        "$returnKey",
        "$showRecordId",
        "$snapshot",
        "$orderby",
        "$collation",
        "$readConcern",
        "$let",
        "$map",
        "$reduce",
        "$zip",
        "$filter",
        "$cond",
        "$ifNull",
        "$switch",
        "$arrayElemAt",
        "$slice",
        "$concat",
        "$substr",
        "$toUpper",
        "$toLower",
        "$dateToString",
        "$convert",
        "$toString",
        "$toInt",
        "$toDouble",
        "$toDecimal",
        "$dateFromString",
        "$dateFromParts",
        "$dateToParts",
        "$year",
        "$month",
        "$dayOfMonth",
        "$hour",
        "$minute",
        "$second",
        "$millisecond",
        "$dayOfWeek",
        "$dayOfYear",
        "$week",
        "$isoWeek",
        "$isoDayOfWeek",
        "$isoWeekYear",
        "$cmp",
    }
)

#: Stages that write, join across collections, or touch Atlas/vector/index
#: subsystems; rejected even though they are valid MongoDB.
_FORBIDDEN_STAGES = frozenset(
    {
        "$lookup",
        "$graphLookup",
        "$facet",
        "$search",
        "$vectorSearch",
        "$out",
        "$merge",
        "$unionWith",
        "$redact",
        "$sample",
        "$geoNear",
        "$bucket",
        "$bucketAuto",
        "$set",
        "$unset",
        "$replaceRoot",
        "$replaceWith",
        "$indexStats",
        "$listLocalSessions",
        "$listSessions",
        "$currentOp",
        "$collStats",
        "$planCacheStats",
        "$shardedDataDistribution",
        "$changeStream",
        "$documents",
        "$densify",
        "$fill",
    }
)

#: Wildcard projection markers that would leak unknown fields.
_WILDCARD_MARKERS = frozenset({"**", "$", "*", "$**"})


@dataclass(frozen=True)
class MongoGuardPolicy:
    """Guard policy: what a validated spec is allowed to touch.

    ``allowed_fields`` carries canonical dotted paths; a referenced path
    must match exactly (no parent/child broadening).  An empty
    ``allowed_collections`` denies every collection (fail closed).
    """

    allowed_collections: frozenset[str] = frozenset()
    allowed_fields: frozenset[str] = frozenset()
    allowed_operators: frozenset[str] = DEFAULT_OPERATORS
    allowed_stages: frozenset[str] = DEFAULT_STAGES
    allowed_expressions: frozenset[str] = DEFAULT_EXPRESSIONS
    max_limit: int = 1_000_000
    max_skip: int = 1_000_000
    max_stages: int = 16
    require_limit: bool = True
    tenant_profile: str | None = None
    required_obligation_fingerprint: str | None = None

    def policy_hash(self) -> str:
        """Canonical fingerprint of the policy used in guard fingerprints."""
        return sha256_fingerprint(
            {
                "allowed_collections": sorted(self.allowed_collections),
                "allowed_fields": sorted(self.allowed_fields),
                "allowed_operators": sorted(self.allowed_operators),
                "allowed_stages": sorted(self.allowed_stages),
                "allowed_expressions": sorted(self.allowed_expressions),
                "max_limit": self.max_limit,
                "max_skip": self.max_skip,
                "max_stages": self.max_stages,
                "require_limit": self.require_limit,
                "tenant_profile": self.tenant_profile,
                "required_obligation_fingerprint": self.required_obligation_fingerprint,
            }
        )


def _guard_fingerprint(spec: MongoQuerySpec, policy: MongoGuardPolicy) -> str:
    from .normalize import mql_spec_fingerprint

    return sha256_fingerprint(
        {
            "spec": mql_spec_fingerprint(spec),
            "policy": policy.policy_hash(),
        }
    )


def _iter_operator_keys(value: Any) -> list[str]:
    """Collect every ``$``-prefixed key anywhere inside a filter value."""
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.startswith("$"):
                keys.append(key)
            keys.extend(_iter_operator_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_iter_operator_keys(item))
    return keys


def _validate_filter(
    filter_: Mapping[str, Any],
    policy: MongoGuardPolicy,
    reasons: list[str],
) -> None:
    for path, value in filter_.items():
        if path not in policy.allowed_fields:
            reasons.append(f"field '{path}' is outside the allowed scope")
        for key in _iter_operator_keys(value):
            if key in _JS_KEYS:
                reasons.append(f"operator '{key}' is not allowed (unsafe construct)")
            elif key not in policy.allowed_operators:
                reasons.append(f"operator '{key}' is not approved by the profile")


def _filter_fingerprints(filter_: Mapping[str, Any]) -> frozenset[str]:
    """Canonical leaf predicates present in a structured filter."""
    from .normalize import predicate_fingerprint

    fingerprints: set[str] = set()
    for path, value in filter_.items():
        if isinstance(value, Mapping) and value and all(
            isinstance(key, str) and key.startswith("$") for key in value
        ):
            for operator, operand in value.items():
                fingerprints.add(predicate_fingerprint(path, operator, operand))
        elif not isinstance(value, Mapping):
            fingerprints.add(predicate_fingerprint(path, "$eq", value))
    return frozenset(fingerprints)


def _spec_filter_fingerprints(spec: MongoQuerySpec) -> frozenset[str]:
    """Predicates that the executor will apply for the operation."""
    fingerprints = set(_filter_fingerprints(spec.filter))
    if spec.pipeline is not None:
        for stage in spec.pipeline:
            if "$match" in stage and isinstance(stage["$match"], Mapping):
                fingerprints.update(_filter_fingerprints(stage["$match"]))
    return frozenset(fingerprints)


def _validate_projection(
    projection: Mapping[str, Any],
    policy: MongoGuardPolicy,
    reasons: list[str],
    *,
    allow_expressions: bool = False,
    derived_paths: frozenset[str] = frozenset(),
) -> None:
    if not projection:
        return
    if allow_expressions:
        # Aggregate $project also accepts bounded rename expressions that
        # reference the group key ("$_id") or an allowed field path, and
        # output paths produced by a preceding $group (derived paths).
        for path, marker in projection.items():
            if path in _WILDCARD_MARKERS or path.endswith(".$") or "$" in path:
                reasons.append(f"wildcard projection '{path}' is not allowed")
                continue
            if path == "_id":
                if marker != 0:
                    reasons.append("'_id' may only be excluded in a projection")
                continue
            if path not in policy.allowed_fields and path not in derived_paths:
                reasons.append(f"projection field '{path}' is outside the allowed scope")
            if isinstance(marker, int) and marker in {0, 1}:
                continue
            if isinstance(marker, str) and (
                marker == "$_id" or (marker.startswith("$") and marker[1:] in policy.allowed_fields)
            ):
                continue
            reasons.append(
                f"projection value for '{path}' must be 1, 0, or a '$path' expression"
            )
        return
    markers = set(projection.values())
    if 0 in markers and 1 in markers:
        reasons.append("projection cannot mix inclusion and exclusion")
    if any(isinstance(marker, str) for marker in markers) and any(
        not isinstance(marker, str) for marker in markers
    ):
        reasons.append("projection cannot mix rename expressions with 0/1 markers")
    for path, marker in projection.items():
        if path in _WILDCARD_MARKERS or path.endswith(".$") or "$" in path:
            reasons.append(f"wildcard projection '{path}' is not allowed")
            continue
        if isinstance(marker, str):
            # Bounded rename "<output>" -> "$<allowed field>"; the output
            # name must not shadow an allowed field so result columns stay
            # unambiguous.
            if not marker.startswith("$") or marker[1:] not in policy.allowed_fields:
                reasons.append(
                    f"projection rename for '{path}' must reference an allowed '$field'"
                )
            elif path in policy.allowed_fields:
                reasons.append(f"projection rename '{path}' shadows an allowed field")
            continue
        if marker not in {0, 1}:
            reasons.append("projection values must be inclusion (1) or exclusion (0) markers")
            continue
        if path not in policy.allowed_fields:
            reasons.append(f"projection field '{path}' is outside the allowed scope")


def _validate_sort(
    sort: Mapping[str, int],
    policy: MongoGuardPolicy,
    reasons: list[str],
    *,
    derived_paths: frozenset[str] = frozenset(),
) -> None:
    for path, direction in sort.items():
        if direction not in {-1, 1}:
            reasons.append(f"sort direction for '{path}' must be 1 or -1")
        if path not in policy.allowed_fields and path not in derived_paths:
            reasons.append(f"sort field '{path}' is outside the allowed scope")


def _validate_aggregate(
    spec: MongoQuerySpec,
    policy: MongoGuardPolicy,
    reasons: list[str],
) -> None:
    pipeline = spec.pipeline
    if pipeline is None:
        reasons.append("aggregate requires a pipeline")
        return
    if len(pipeline) > policy.max_stages:
        reasons.append(
            f"pipeline has {len(pipeline)} stages, exceeding the maximum {policy.max_stages}"
        )
    bounded = False
    #: Output paths produced by a preceding $group (its _id key and
    #: accumulator aliases); they are derived, not source field accesses.
    derived: set[str] = set()
    has_result_shape = False
    for index, stage in enumerate(pipeline):
        if not isinstance(stage, Mapping) or len(stage) != 1:
            reasons.append(f"pipeline stage {index} must be a single-key object")
            continue
        name, argument = next(iter(stage.items()))
        if name in _FORBIDDEN_STAGES:
            reasons.append(f"pipeline stage '{name}' is not allowed")
            continue
        if name not in policy.allowed_stages:
            reasons.append(f"pipeline stage '{name}' is not approved by the profile")
            continue
        if name == "$match":
            if not isinstance(argument, Mapping):
                reasons.append("$match requires a filter object")
            else:
                _validate_filter(argument, policy, reasons)
        elif name == "$project":
            if not isinstance(argument, Mapping):
                reasons.append("$project requires a projection object")
            else:
                _validate_projection(
                    argument,
                    policy,
                    reasons,
                    allow_expressions=True,
                    derived_paths=frozenset(derived),
                )
                if not any(
                    marker == 1 or isinstance(marker, str)
                    for path, marker in argument.items()
                    if path != "_id"
                ):
                    reasons.append(
                        "$project must explicitly include or rename at least one output field"
                    )
                else:
                    has_result_shape = True
        elif name == "$sort":
            if not isinstance(argument, Mapping):
                reasons.append("$sort requires a sort object")
            else:
                _validate_sort(
                    argument, policy, reasons, derived_paths=frozenset(derived)
                )
        elif name == "$skip":
            if not isinstance(argument, int) or argument < 0 or argument > policy.max_skip:
                reasons.append(
                    f"$skip must be an integer between 0 and {policy.max_skip}"
                )
        elif name == "$limit":
            if not isinstance(argument, int) or argument < 1 or argument > policy.max_limit:
                reasons.append(
                    f"$limit must be an integer between 1 and {policy.max_limit}"
                )
            else:
                bounded = index == len(pipeline) - 1
        elif name == "$group":
            _validate_group(argument, policy, reasons)
            if isinstance(argument, Mapping):
                derived.add("_id")
                derived.update(alias for alias in argument if alias != "_id")
                has_result_shape = True
        elif name == "$count":
            if not isinstance(argument, str) or not argument:
                reasons.append("$count requires a non-empty output field name")
            else:
                has_result_shape = True
        elif name == "$unwind":
            if isinstance(argument, str):
                if not argument.startswith("$") or argument[1:] not in policy.allowed_fields:
                    reasons.append(
                        f"$unwind path '{argument}' is outside the allowed scope"
                    )
            elif isinstance(argument, Mapping) and set(argument) == {"path"}:
                path = argument["path"]
                if not isinstance(path, str) or path[1:] not in policy.allowed_fields:
                    reasons.append(f"$unwind path '{path}' is outside the allowed scope")
            else:
                reasons.append("$unwind requires a path string or a {'path': ...} object")
    if not bounded and spec.limit is None:
        reasons.append("aggregate output is unbounded; add a $limit stage or a spec limit")
    elif spec.limit is not None and spec.limit > policy.max_limit:
        reasons.append(f"limit {spec.limit} exceeds the maximum bounded rows {policy.max_limit}")
    if not has_result_shape:
        reasons.append(
            "aggregate pipeline must include $project, $group, or $count to constrain output fields"
        )


def _validate_group(argument: Any, policy: MongoGuardPolicy, reasons: list[str]) -> None:
    if not isinstance(argument, Mapping) or "_id" not in argument:
        reasons.append("$group requires an _id key")
        return
    group_id = argument["_id"]
    if group_id is not None:
        if not isinstance(group_id, str) or not group_id.startswith("$"):
            reasons.append("$group _id must be null or a '$path' expression")
        elif group_id[1:] not in policy.allowed_fields:
            reasons.append(f"$group _id path '{group_id}' is outside the allowed scope")
    for alias, expression in argument.items():
        if alias == "_id":
            continue
        if not isinstance(alias, str) or not alias:
            reasons.append("$group accumulator aliases must be non-empty strings")
            continue
        if not isinstance(expression, Mapping) or len(expression) != 1:
            reasons.append(f"$group accumulator '{alias}' must be a single-expression object")
            continue
        expr_name, expr_value = next(iter(expression.items()))
        if expr_name not in policy.allowed_expressions:
            reasons.append(f"$group expression '{expr_name}' is not approved by the profile")
            continue
        if isinstance(expr_value, int):
            if expr_name == "$sum" and expr_value != 1:
                reasons.append("$sum constant must be 1 (counting)")
        elif isinstance(expr_value, str):
            if not expr_value.startswith("$") or expr_value[1:] not in policy.allowed_fields:
                reasons.append(
                    f"$group expression path '{expr_value}' is outside the allowed scope"
                )
        else:
            reasons.append(f"$group expression value for '{alias}' must be 1 or a '$path'")


def _validate_tenant(
    spec: MongoQuerySpec,
    policy: MongoGuardPolicy,
    reasons: list[str],
) -> None:
    profile = policy.tenant_profile
    if profile is None:
        return
    if profile == "pooled":
        obligation = spec.tenant_obligation
        if obligation is None:
            reasons.append("pooled tenant profile requires a verified tenant obligation")
            return
        if (
            policy.required_obligation_fingerprint is not None
            and obligation.fingerprint != policy.required_obligation_fingerprint
        ):
            reasons.append("tenant obligation fingerprint does not match the required one")
        if obligation.field_id not in policy.allowed_fields:
            reasons.append(
                f"tenant obligation field '{obligation.field_id}' is outside the allowed scope"
            )
        if obligation.operator not in policy.allowed_operators:
            reasons.append(
                f"tenant obligation operator '{obligation.operator}' is not approved"
            )
        if obligation.fingerprint not in _spec_filter_fingerprints(spec):
            reasons.append("tenant obligation is not enforced by the query filter")
        return
    routing = spec.routing_evidence
    if routing is None:
        reasons.append(
            f"{profile} tenant profile requires verified routing evidence"
        )
        return
    expected = profile.removesuffix("_isolated")
    if routing.kind.value != expected:
        reasons.append(
            f"routing evidence kind '{routing.kind.value}' does not match profile '{profile}'"
        )


def run_guard(spec: MongoQuerySpec, policy: MongoGuardPolicy) -> MongoGuardResult:
    """Evaluate the guard against a typed spec; never raises for denials."""
    reasons: list[str] = []

    if policy.allowed_collections and spec.collection not in policy.allowed_collections:
        reasons.append(f"collection '{spec.collection}' is outside the allowed scope")
    elif not policy.allowed_collections:
        reasons.append("no collections are allowed by the guard policy")

    _validate_filter(spec.filter, policy, reasons)
    _validate_projection(spec.projection, policy, reasons)
    _validate_sort(spec.sort, policy, reasons)

    if spec.skip is not None and spec.skip > policy.max_skip:
        reasons.append(f"skip {spec.skip} exceeds the maximum {policy.max_skip}")

    if spec.operation == MongoOperation.FIND:
        if not spec.projection:
            reasons.append("find requires an explicit projection to constrain output fields")
        if policy.require_limit and spec.limit is None:
            reasons.append("a bounded result is required but the spec has no limit")
        elif spec.limit is not None and spec.limit > policy.max_limit:
            reasons.append(
                f"limit {spec.limit} exceeds the maximum bounded rows {policy.max_limit}"
            )
    elif spec.operation == MongoOperation.COUNT:
        if spec.limit is not None and spec.limit > policy.max_limit:
            reasons.append(
                f"limit {spec.limit} exceeds the maximum bounded rows {policy.max_limit}"
            )
    else:
        if spec.filter or spec.projection or spec.sort or spec.skip is not None:
            reasons.append(
                "aggregate filter, projection, sort, and skip must be expressed in the pipeline"
            )
        _validate_aggregate(spec, policy, reasons)

    _validate_tenant(spec, policy, reasons)

    return MongoGuardResult(
        accepted=not reasons,
        reasons=tuple(reasons),
        fingerprint=_guard_fingerprint(spec, policy),
    )


def assert_validated(spec: MongoQuerySpec, policy: MongoGuardPolicy) -> MongoGuardResult:
    """Run the guard and raise :class:`MongoAdapterError` when rejected."""
    result = run_guard(spec, policy)
    if not result.accepted:
        raise MongoAdapterError(
            "specification was rejected by the MongoDB guard",
            details={"reasons": "; ".join(result.reasons)},
        )
    return result
