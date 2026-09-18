import re
from pathlib import Path
from typing import Any

import yaml

from core.settings import config
from utils.confirmed_business import load_rules

step_config = {
    "step": 4,
    "dimension": "completeness",
    "depend_business": [],
}

NULL_CLASSIFIED_FILE = "completeness/col_null_classified.yaml"
NOT_NULLABLE_FILE = "completeness/col_notnullable.yaml"
VALID_NULL_CLASSIFICATIONS = {
    "Valid NULL",
    "Conditional NULL",
    "Invalid NULL",
    "Needs business confirmation",
}


def _rules_path(relative_path: str) -> Path:
    return (Path(config.base_dir) / "confirmed_business" / relative_path).resolve()


def _load_rule_document(relative_path: str) -> dict[str, Any]:
    path = _rules_path(relative_path)
    if not path.is_file():
        return {"version": 1, "status": "draft", "rules": []}
    with path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{relative_path} must contain a YAML mapping")
    if not isinstance(document.get("rules", []), list):
        raise ValueError(f"{relative_path} rules must be a list")
    return document


def _write_rule_document(relative_path: str, document: dict[str, Any]) -> None:
    path = _rules_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(document, file, allow_unicode=True, sort_keys=False)


def _is_null_candidate(row: dict[str, Any]) -> bool:
    return row.get("null_warning") in {"🔴 Nguy hiểm", "🟡 Nghi ngờ"}


def _auto_classify_column(column_name: str) -> str | None:
    name = column_name.lower()
    document = load_rules("column_constraints.yaml")
    for rule in document.get("rules", []):
        if not isinstance(rule, dict):
            continue
        for pattern in rule.get("patterns", []):
            if isinstance(pattern, str) and re.search(pattern, name, re.IGNORECASE):
                value = rule.get("null_classify")
                return value if isinstance(value, str) else None
    return None


def prepare_column_null_rules(rows: list[dict[str, Any]]) -> Path:
    """Create/update NULL-classification rules for warning columns."""
    document = _load_rule_document(NULL_CLASSIFIED_FILE)
    document["status"] = "confirmed"
    rules = document.setdefault("rules", [])
    rules_by_column = {
        (str(rule.get("schema")), str(rule.get("table")), str(rule.get("column"))): rule
        for rule in rules
        if isinstance(rule, dict)
        and rule.get("schema")
        and rule.get("table")
        and rule.get("column")
    }

    for row in rows:
        if not _is_null_candidate(row):
            continue
        key = (row["schema_name"], row["table_name"], row["column_name"])
        rule = rules_by_column.get(key)
        if rule is None:
            auto_value = _auto_classify_column(row["column_name"])
            rule = {
                "id": f"auto-{row['schema_name']}-{row['table_name']}-{row['column_name']}",
                "status": "confirmed" if auto_value else "need_check",
                "owner": "system" if auto_value else "data-owner",
                "schema": row["schema_name"],
                "table": row["table_name"],
                "column": row["column_name"],
                "null_classify": auto_value,
            }
            rules.append(rule)
            rules_by_column[key] = rule
            continue

        value = rule.get("null_classify") or rule.get("classification")
        if isinstance(value, str) and value.strip():
            rule["null_classify"] = value.strip()
            rule["status"] = "confirmed"
            continue

        auto_value = _auto_classify_column(row["column_name"])
        if auto_value:
            rule["status"] = "confirmed"
            rule["owner"] = "system"
            rule["null_classify"] = auto_value
        else:
            rule["status"] = "need_check"
            rule["null_classify"] = None

    path = _rules_path(NULL_CLASSIFIED_FILE)
    _write_rule_document(NULL_CLASSIFIED_FILE, document)
    return path


def check_column_null_rules_complete(rows: list[dict[str, Any]]) -> bool:
    """Return whether every NULL-warning column has a classification."""
    document = _load_rule_document(NULL_CLASSIFIED_FILE)
    rules = {
        (str(rule.get("schema")), str(rule.get("table")), str(rule.get("column"))): rule
        for rule in document.get("rules", [])
        if isinstance(rule, dict)
    }
    for row in rows:
        if not _is_null_candidate(row):
            continue
        rule = rules.get(
            (str(row["schema_name"]), str(row["table_name"]), str(row["column_name"])),
            {},
        )
        value = rule.get("null_classify") or rule.get("classification")
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def classify_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add confirmed NotNullable and NULL classification to column rows."""
    not_nullable_rules = load_rules(NOT_NULLABLE_FILE).get("rules", [])
    null_classification_rules = load_rules(NULL_CLASSIFIED_FILE).get("rules", [])

    not_nullable_by_column = {}
    for rule in not_nullable_rules:
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            continue
        key = (rule.get("schema"), rule.get("table"), rule.get("column"))
        value = rule.get("not_nullable", rule.get("value"))
        if value is not None:
            not_nullable_by_column[key] = value

    null_classification_by_column = {}
    for rule in null_classification_rules:
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            continue
        key = (rule.get("schema"), rule.get("table"), rule.get("column"))
        value = rule.get("null_classify", rule.get("classification"))
        if value is not None:
            null_classification_by_column[key] = value

    return [
        {
            **row,
            "not_nullable": not_nullable_by_column.get(
                (row["schema_name"], row["table_name"], row["column_name"]), ""
            ),
            "null_classify": null_classification_by_column.get(
                (row["schema_name"], row["table_name"], row["column_name"]), ""
            ),
        }
        for row in rows
    ]
