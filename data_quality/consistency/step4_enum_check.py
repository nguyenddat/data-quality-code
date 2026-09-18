import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from core.database import get_connection
from core.settings import config
from utils.confirmed_business import load_rules
from utils.formatter import parse_column_reference, quote_identifier


step_config = {
    "step": 4,
    "dimension": "consistency",
    "depend_business": [],
}

ENUM_RULES_FILE = "consistency/enum_checks.yaml"
ENUM_NAME_PATTERN = re.compile(
    r"(^|_)(loai|tinh_trang|trang_thai|gioi_tinh|nhom_mau|status|state)(_|$)",
    re.IGNORECASE,
)


def _rules_path() -> Path:
    return Path(config.base_dir) / "confirmed_business" / ENUM_RULES_FILE


def _load_enum_document() -> dict[str, Any]:
    path = _rules_path()
    if not path.is_file():
        return {
            "version": 1,
            "dimension": "consistency",
            "step": "enum_check",
            "status": "draft",
            "rules": [],
        }
    with path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}
    if not isinstance(document, dict) or not isinstance(document.get("rules", []), list):
        raise ValueError("enum_checks.yaml must contain a mapping with a rules list")
    return document


def _write_enum_document(document: dict[str, Any]) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(document, file, allow_unicode=True, sort_keys=False)


def _is_enum_candidate(column_name: str) -> bool:
    return ENUM_NAME_PATTERN.search(column_name) is not None


def _auto_detect_enum_rules(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = []
    for row in rows:
        column = str(row["column_name"])
        if not _is_enum_candidate(column):
            continue
        schema = str(row["schema_name"])
        table = str(row["table_name"])
        rules.append(
            {
                "id": f"auto-{schema}-{table}-{column}-enum",
                "status": "need_check",
                "owner": "data-owner",
                "pattern": "enum",
                "column": f"{schema}.{table}.{column}",
                "allowed_values": [],
                "note": "Data owner bổ sung allowed_values theo nghiệp vụ rồi chuyển status thành confirmed.",
            }
        )
    return rules


def prepare_enum_rules(rows: list[dict[str, Any]]) -> Path:
    """Create/update enum candidates using column-name rules."""
    document = _load_enum_document()
    rules = document.setdefault("rules", [])
    existing_ids = {rule.get("id") for rule in rules if isinstance(rule, dict)}

    for rule in _auto_detect_enum_rules(rows):
        if rule["id"] not in existing_ids:
            rules.append(rule)
            existing_ids.add(rule["id"])

    document["status"] = "confirmed" if any(
        isinstance(rule, dict) and rule.get("status") == "confirmed" for rule in rules
    ) else "draft"
    _write_enum_document(document)
    return _rules_path()


def _validate_rule(rule: dict[str, Any]) -> None:
    required = ("id", "column", "allowed_values")
    missing = [field for field in required if field not in rule]
    if missing:
        raise ValueError(f"Enum rule is missing: {', '.join(missing)}")
    if rule.get("pattern") != "enum":
        raise ValueError(f"Unsupported enum pattern: {rule.get('pattern')}")
    if "column" in rule:
        parse_column_reference(rule["column"])
    elif not all(rule.get(field) for field in ("schema", "table", "column")):
        raise ValueError("Enum rule must define column as schema.table.column")
    if not isinstance(rule["allowed_values"], list) or not rule["allowed_values"]:
        raise ValueError(f"Enum rule {rule['id']} must define allowed_values")


def check_enum_rules_complete() -> bool:
    """Return whether every prepared enum rule has confirmed allowed values."""
    document = _load_enum_document()
    for rule in document.get("rules", []):
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            return False
        _validate_rule(rule)
    return True


def load_confirmed_enum_rules() -> list[dict[str, Any]]:
    """Load only confirmed enum rules with a non-empty allowed set."""
    rules = []
    for rule in load_rules(ENUM_RULES_FILE).get("rules", []):
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            continue
        _validate_rule(rule)
        rules.append(rule)
    return rules


def _build_enum_query(rule: dict[str, Any]) -> tuple[str, list[Any]]:
    _validate_rule(rule)
    if "." in str(rule["column"]):
        schema, table_name, column_name = parse_column_reference(rule["column"])
    else:
        schema, table_name, column_name = (
            rule["schema"],
            rule["table"],
            rule["column"],
        )
    table = f"{quote_identifier(schema)}.{quote_identifier(table_name)}"
    column = quote_identifier(column_name)
    placeholders = ", ".join("?" for _ in rule["allowed_values"])
    query = f"""
WITH non_null_values AS (
    SELECT {column} AS enum_value
    FROM {table}
    WHERE {column} IS NOT NULL
), invalid_values AS (
    SELECT CAST(enum_value AS VARCHAR) AS invalid_value
    FROM non_null_values
    WHERE enum_value NOT IN ({placeholders})
)
SELECT
    (SELECT COUNT(*) FROM non_null_values) AS checked_count,
    COUNT(*) AS invalid_count,
    COUNT(DISTINCT invalid_value) AS invalid_distinct_count,
    COALESCE(STRING_AGG(DISTINCT invalid_value, ', '), '') AS invalid_values
FROM invalid_values;
"""
    return query, list(rule["allowed_values"])


def _make_result(
    rule: dict[str, Any],
    checked_count: int,
    invalid_count: int,
    invalid_distinct_count: int,
    invalid_values: str,
) -> dict[str, Any]:
    invalid_pct = round((invalid_count / checked_count) * 100, 2) if checked_count else 0.0
    return {
        "rule_id": rule["id"],
        "column": rule["column"],
        "checked_count": checked_count,
        "invalid_count": invalid_count,
        "invalid_distinct_count": invalid_distinct_count,
        "invalid_values": invalid_values,
        "invalid_pct": invalid_pct,
    }


def load_enum_violations(
    rules: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Measure values outside confirmed enum sets using read-only queries."""
    selected_rules = load_confirmed_enum_rules() if rules is None else rules
    if not selected_rules:
        return []

    results = []
    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            for rule in selected_rules:
                query, parameters = _build_enum_query(rule)
                cursor.execute(query, parameters)
                (
                    checked_count,
                    invalid_count,
                    invalid_distinct_count,
                    invalid_values,
                ) = cursor.fetchone()
                results.append(
                    _make_result(
                        rule,
                        int(checked_count or 0),
                        int(invalid_count or 0),
                        int(invalid_distinct_count or 0),
                        invalid_values or "",
                    )
                )
        finally:
            cursor.close()
    return results
