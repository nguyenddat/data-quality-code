from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from core.database import get_connection
from core.settings import config
from data_quality.consistency.step1_table_relationship import Relationship
from utils.confirmed_business import load_rules
from utils.formatter import parse_column_reference, quote_identifier


step_config = {
    "step": 5,
    "dimension": "consistency",
    "depend_business": [],
}

CROSS_TABLE_RULES_FILE = "consistency/cross_table_checks.yaml"


def _rules_path() -> Path:
    return Path(config.base_dir) / "confirmed_business" / CROSS_TABLE_RULES_FILE


def _load_rule_document() -> dict[str, Any]:
    path = _rules_path()
    if not path.is_file():
        return {
            "version": 1,
            "dimension": "consistency",
            "step": "cross_table_check",
            "status": "draft",
            "rules": [],
        }
    with path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}
    if not isinstance(document, dict) or not isinstance(document.get("rules", []), list):
        raise ValueError("cross_table_checks.yaml must contain a mapping with a rules list")
    return document


def _write_rule_document(document: dict[str, Any]) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(document, file, allow_unicode=True, sort_keys=False)


def _group_relationships(
    relationships: Iterable[Relationship],
) -> list[list[Relationship]]:
    grouped: dict[tuple[str, str, str], list[Relationship]] = defaultdict(list)
    for relationship in relationships:
        grouped[
            (
                relationship["from_schema"],
                relationship["from_table"],
                relationship["constraint_name"],
            )
        ].append(relationship)
    return [list(group) for group in grouped.values()]


def _column_index(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], set[str]]:
    columns: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        columns[(str(row["schema_name"]), str(row["table_name"]))].add(
            str(row["column_name"])
        )
    return columns


def _auto_detect_rules(
    rows: list[dict[str, Any]],
    relationships: Iterable[Relationship],
) -> list[dict[str, Any]]:
    """Suggest duplicated-value checks for same-named columns on FK-related tables."""
    columns = _column_index(rows)
    rules = []
    for relationship_group in _group_relationships(relationships):
        first = relationship_group[0]
        child_key = (first["from_schema"], first["from_table"])
        parent_key = (first["to_schema"], first["to_table"])
        join_columns = [
            {
                "source": (
                    f"{item['to_schema']}.{item['to_table']}.{item['to_column']}"
                ),
                "target": (
                    f"{item['from_schema']}.{item['from_table']}.{item['from_column']}"
                ),
            }
            for item in sorted(
                relationship_group,
                key=lambda item: item.get("column_position") or 0,
            )
        ]
        for column in sorted(columns[parent_key] & columns[child_key]):
            if column in {item["from_column"] for item in relationship_group}:
                continue
            source = f"{first['to_schema']}.{first['to_table']}.{column}"
            target = f"{first['from_schema']}.{first['from_table']}.{column}"
            rules.append(
                {
                    "id": (
                        f"auto-{first['from_schema']}-{first['from_table']}-"
                        f"{first['to_table']}-{column}-cross-table"
                    ),
                    "status": "need_check",
                    "owner": "data-owner",
                    "pattern": "duplicated_value",
                    "source": source,
                    "target": target,
                    "join": join_columns,
                    "null_policy": "exclude_if_either_null",
                    "note": "Data owner xác nhận hai cột phải nhất quán theo nghiệp vụ.",
                }
            )
    return rules


def prepare_cross_table_rules(
    rows: list[dict[str, Any]],
    relationships: Iterable[Relationship],
) -> Path:
    """Create/update cross-table candidates from Step 1 relationships."""
    document = _load_rule_document()
    rules = document.setdefault("rules", [])
    existing_ids = {rule.get("id") for rule in rules if isinstance(rule, dict)}
    for rule in _auto_detect_rules(rows, relationships):
        if rule["id"] not in existing_ids:
            rules.append(rule)
            existing_ids.add(rule["id"])
    document["status"] = "confirmed" if any(
        isinstance(rule, dict) and rule.get("status") == "confirmed" for rule in rules
    ) else "draft"
    _write_rule_document(document)
    return _rules_path()


def _validate_rule(rule: dict[str, Any]) -> None:
    required = ("id", "pattern", "source", "target", "join")
    missing = [field for field in required if field not in rule]
    if missing:
        raise ValueError(f"Cross-table rule is missing: {', '.join(missing)}")
    if rule["pattern"] != "duplicated_value":
        raise ValueError(f"Unsupported cross-table pattern: {rule['pattern']}")
    parse_column_reference(rule["source"])
    parse_column_reference(rule["target"])
    if not isinstance(rule["join"], list) or not rule["join"]:
        raise ValueError(f"Cross-table rule {rule['id']} must define join")
    for pair in rule["join"]:
        if not isinstance(pair, dict) or not pair.get("source") or not pair.get("target"):
            raise ValueError(f"Cross-table rule {rule['id']} has invalid join")
        parse_column_reference(pair["source"])
        parse_column_reference(pair["target"])
    if rule.get("null_policy", "exclude_if_either_null") not in {
        "exclude_if_either_null",
        "nulls_are_different",
    }:
        raise ValueError(f"Unsupported null_policy in rule {rule['id']}")


def check_cross_table_rules_complete() -> bool:
    """Return whether all prepared cross-table rules are confirmed and valid."""
    document = _load_rule_document()
    for rule in document["rules"]:
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            return False
        _validate_rule(rule)
    return True


def load_confirmed_cross_table_rules() -> list[dict[str, Any]]:
    """Load only confirmed cross-table rules."""
    rules = []
    for rule in load_rules(CROSS_TABLE_RULES_FILE).get("rules", []):
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            continue
        _validate_rule(rule)
        rules.append(rule)
    return rules


def _build_cross_table_query(rule: dict[str, Any]) -> str:
    _validate_rule(rule)
    source_schema, source_table, source_column = parse_column_reference(rule["source"])
    target_schema, target_table, target_column = parse_column_reference(rule["target"])
    source_alias = "source_record"
    target_alias = "target_record"

    join_conditions = []
    for pair in rule["join"]:
        source_join = parse_column_reference(pair["source"])
        target_join = parse_column_reference(pair["target"])
        join_conditions.append(
            f"{source_alias}.{quote_identifier(source_join[2])} = "
            f"{target_alias}.{quote_identifier(target_join[2])}"
        )

    source_value = f"{source_alias}.{quote_identifier(source_column)}"
    target_value = f"{target_alias}.{quote_identifier(target_column)}"
    if rule.get("null_policy", "exclude_if_either_null") == "exclude_if_either_null":
        sample_filter = f"{source_value} IS NOT NULL AND {target_value} IS NOT NULL"
    else:
        sample_filter = "TRUE"

    source_table_ref = f"{quote_identifier(source_schema)}.{quote_identifier(source_table)}"
    target_table_ref = f"{quote_identifier(target_schema)}.{quote_identifier(target_table)}"
    return f"""
SELECT
    COUNT(*) AS checked_count,
    COALESCE(SUM(
        CASE WHEN {source_value} IS DISTINCT FROM {target_value}
             THEN 1 ELSE 0 END
    ), 0) AS violation_count
FROM {source_table_ref} AS {source_alias}
JOIN {target_table_ref} AS {target_alias}
  ON {' AND '.join(join_conditions)}
WHERE {sample_filter};
"""


def _make_result(
    rule: dict[str, Any], checked_count: int, violation_count: int
) -> dict[str, Any]:
    violation_pct = round((violation_count / checked_count) * 100, 2) if checked_count else 0.0
    return {
        "rule_id": rule["id"],
        "pattern": rule["pattern"],
        "source": rule["source"],
        "target": rule["target"],
        "checked_count": checked_count,
        "violation_count": violation_count,
        "violation_pct": violation_pct,
    }


def load_cross_table_violations(
    rules: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Measure confirmed duplicated-value rules using read-only queries."""
    selected_rules = load_confirmed_cross_table_rules() if rules is None else rules
    if not selected_rules:
        return []

    results = []
    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            for rule in selected_rules:
                cursor.execute(_build_cross_table_query(rule))
                checked_count, violation_count = cursor.fetchone()
                results.append(
                    _make_result(
                        rule,
                        int(checked_count or 0),
                        int(violation_count or 0),
                    )
                )
        finally:
            cursor.close()
    return results
