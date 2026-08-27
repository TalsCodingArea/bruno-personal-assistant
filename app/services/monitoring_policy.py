"""Compile controlled Financial Rules entries into deterministic monitoring policy."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domain.monitoring import MonitoringPolicy, MonitoringPolicySource
from app.domain.profile import FinancialProfileEntry, ProfileStatus

PROJECTION_BANDS_KEY = "monitoring.projection_bands"
PROTECTED_SUBCATEGORIES_KEY = "monitoring.protected_subcategories"
PREFERRED_DONORS_KEY = "monitoring.preferred_donor_subcategories"
AUTOMATIC_ADJUSTMENTS_KEY = "monitoring.automatic_budget_adjustments_enabled"
MINIMUM_MATERIAL_AMOUNT_KEY = "monitoring.alert.minimum_material_amount"
ALERT_COOLDOWN_HOURS_KEY = "monitoring.alert.cooldown_hours"
MATERIAL_PROJECTION_INCREASE_KEY = "monitoring.alert.material_projection_increase"
REALLOCATION_ENABLED_KEY = "monitoring.reallocation.enabled"
REALLOCATION_MAX_AMOUNT_KEY = "monitoring.reallocation.max_amount"
EMERGENCY_BUFFER_AMOUNT_KEY = "monitoring.emergency_buffer_amount"
MONITORING_POLICY_KEYS = (
    PROJECTION_BANDS_KEY,
    PROTECTED_SUBCATEGORIES_KEY,
    PREFERRED_DONORS_KEY,
    AUTOMATIC_ADJUSTMENTS_KEY,
    MINIMUM_MATERIAL_AMOUNT_KEY,
    ALERT_COOLDOWN_HOURS_KEY,
    MATERIAL_PROJECTION_INCREASE_KEY,
    REALLOCATION_ENABLED_KEY,
    REALLOCATION_MAX_AMOUNT_KEY,
    EMERGENCY_BUFFER_AMOUNT_KEY,
)


class MonitoringPolicyConfigurationError(ValueError):
    """A recognized Active rule cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class CompiledMonitoringPolicy:
    """Effective policy plus compact provenance for every applied override."""

    policy: MonitoringPolicy
    sources: tuple[MonitoringPolicySource, ...]


def compile_monitoring_policy(
    entries: Sequence[FinancialProfileEntry],
    *,
    defaults: MonitoringPolicy | None = None,
) -> CompiledMonitoringPolicy:
    """Apply exact, JSON-valued policy keys and reject ambiguous configuration."""

    base = defaults or MonitoringPolicy()
    recognized: dict[str, FinancialProfileEntry] = {}
    for entry in entries:
        key = entry.key.strip().casefold()
        if entry.status is not ProfileStatus.ACTIVE or key not in MONITORING_POLICY_KEYS:
            continue
        if key in recognized:
            raise MonitoringPolicyConfigurationError(
                f"Multiple Active Financial Rules pages found for {key!r}"
            )
        recognized[key] = entry

    projection_bands = base.projection_bands
    protected = base.protected_subcategories
    preferred = base.preferred_donor_subcategories
    automatic_adjustments = base.automatic_budget_adjustments_enabled
    minimum_material_amount = base.minimum_material_amount
    alert_cooldown_hours = base.alert_cooldown_hours
    material_projection_increase = base.material_projection_increase
    reallocation_enabled = base.reallocation_enabled
    reallocation_max_amount = base.reallocation_max_amount
    emergency_buffer_amount = base.emergency_buffer_amount

    projection_entry = recognized.get(PROJECTION_BANDS_KEY)
    if projection_entry is not None:
        projection_bands = _decimal_array(projection_entry)
    protected_entry = recognized.get(PROTECTED_SUBCATEGORIES_KEY)
    if protected_entry is not None:
        protected = _string_array(protected_entry)
    preferred_entry = recognized.get(PREFERRED_DONORS_KEY)
    if preferred_entry is not None:
        preferred = _string_array(preferred_entry)
    automatic_entry = recognized.get(AUTOMATIC_ADJUSTMENTS_KEY)
    if automatic_entry is not None:
        automatic_adjustments = _boolean(automatic_entry)
    minimum_entry = recognized.get(MINIMUM_MATERIAL_AMOUNT_KEY)
    if minimum_entry is not None:
        minimum_material_amount = _decimal(minimum_entry)
    cooldown_entry = recognized.get(ALERT_COOLDOWN_HOURS_KEY)
    if cooldown_entry is not None:
        alert_cooldown_hours = _positive_integer(cooldown_entry)
    increase_entry = recognized.get(MATERIAL_PROJECTION_INCREASE_KEY)
    if increase_entry is not None:
        material_projection_increase = _decimal(increase_entry)
    reallocation_entry = recognized.get(REALLOCATION_ENABLED_KEY)
    if reallocation_entry is not None:
        reallocation_enabled = _boolean(reallocation_entry)
    reallocation_max_entry = recognized.get(REALLOCATION_MAX_AMOUNT_KEY)
    if reallocation_max_entry is not None:
        reallocation_max_amount = _decimal(reallocation_max_entry)
    emergency_buffer_entry = recognized.get(EMERGENCY_BUFFER_AMOUNT_KEY)
    if emergency_buffer_entry is not None:
        emergency_buffer_amount = _decimal(emergency_buffer_entry)

    try:
        policy = MonitoringPolicy(
            projection_bands=projection_bands,
            protected_subcategories=protected,
            preferred_donor_subcategories=preferred,
            automatic_budget_adjustments_enabled=automatic_adjustments,
            minimum_material_amount=minimum_material_amount,
            alert_cooldown_hours=alert_cooldown_hours,
            material_projection_increase=material_projection_increase,
            reallocation_enabled=reallocation_enabled,
            reallocation_max_amount=reallocation_max_amount,
            emergency_buffer_amount=emergency_buffer_amount,
        )
    except ValueError as exc:
        raise MonitoringPolicyConfigurationError(
            f"Invalid monitoring policy: {exc}"
        ) from exc

    sources = tuple(
        MonitoringPolicySource(
            key=key,
            page_id=recognized[key].page_id,
            operation_id=recognized[key].operation_id,
            last_edited_at=recognized[key].last_edited_at,
        )
        for key in MONITORING_POLICY_KEYS
        if key in recognized
    )
    return CompiledMonitoringPolicy(policy=policy, sources=sources)


def _json_array(entry: FinancialProfileEntry) -> list[Any]:
    try:
        value = json.loads(
            entry.statement,
            parse_float=Decimal,
            parse_int=Decimal,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain a JSON array in Statement"
        ) from exc
    if not isinstance(value, list):
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain a JSON array in Statement"
        )
    return value


def _decimal_array(entry: FinancialProfileEntry) -> tuple[Decimal, ...]:
    values = _json_array(entry)
    if not values or any(not isinstance(value, Decimal) for value in values):
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must be a non-empty JSON array of numbers"
        )
    return tuple(values)


def _string_array(entry: FinancialProfileEntry) -> tuple[str, ...]:
    values = _json_array(entry)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must be a JSON array of non-empty strings"
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        identity = normalized.casefold()
        if identity not in seen:
            result.append(normalized)
            seen.add(identity)
    return tuple(result)


def _boolean(entry: FinancialProfileEntry) -> bool:
    try:
        value = json.loads(entry.statement)
    except json.JSONDecodeError as exc:
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain JSON true or false"
        ) from exc
    if not isinstance(value, bool):
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain JSON true or false"
        )
    return value


def _decimal(entry: FinancialProfileEntry) -> Decimal:
    try:
        value = json.loads(
            entry.statement,
            parse_float=Decimal,
            parse_int=Decimal,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain a JSON number"
        ) from exc
    if not isinstance(value, Decimal):
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain a JSON number"
        )
    return value


def _positive_integer(entry: FinancialProfileEntry) -> int:
    value = _decimal(entry)
    if value <= 0 or value != value.to_integral_value():
        raise MonitoringPolicyConfigurationError(
            f"Financial Rule {entry.key!r} must contain a positive JSON integer"
        )
    return int(value)
