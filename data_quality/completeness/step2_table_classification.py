import re
from pathlib import Path
from typing import Any

import yaml

from core.settings import config
from utils.confirmed_business import ensure_table_tiers_file, load_rules, load_table_tiers

step_config = {
    "step": 2,
    "dimension": "completeness",
    "depend_business": [],
}


def _keyword_matches(table_name: str, keyword: str) -> bool:
    """Match a keyword as a table-name token, including underscore boundaries."""
    token = keyword.strip("_")
    if not token:
        return False
    pattern = rf"(?:^|_){re.escape(token)}(?:_|$)"
    return re.search(pattern, table_name, flags=re.IGNORECASE) is not None


def _auto_classify_tier(table_name: str) -> int | None:
    """Suggest Tier 2 or 3 from the shared constraints file."""
    name = table_name.lower()
    rules = load_rules("tier_constraints.yaml").get("rules", [])
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("tier") not in (2, 3):
            continue
        keywords = rule.get("keywords", [])
        if isinstance(keywords, list) and any(
            isinstance(keyword, str) and _keyword_matches(name, keyword)
            for keyword in keywords
        ):
            return rule["tier"]
    return None


def _table_tiers_path() -> Path:
    return Path(config.business_cf_file("completeness")) / "table_tiers.yaml"


def _normalize_tier(value: Any) -> int | None:
    try:
        tier = int(value)
    except (TypeError, ValueError):
        return None
    return tier if tier in (1, 2, 3) else None


def _load_table_tier_document() -> dict[str, Any]:
    ensure_table_tiers_file()
    file_path = _table_tiers_path()
    with file_path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}
    if not isinstance(document, dict):
        raise ValueError("table_tiers.yaml must contain a YAML mapping")
    if not isinstance(document.get("rules", []), list):
        raise ValueError("table_tiers.yaml rules must be a list")
    return document


def _write_table_tier_document(document: dict[str, Any]) -> None:
    with _table_tiers_path().open("w", encoding="utf-8") as file:
        yaml.safe_dump(document, file, allow_unicode=True, sort_keys=False)


def prepare_table_tiers(rows: list[dict[str, Any]]) -> Path:
    """Create/update table_tiers.yaml with suggestions and need_check rows."""
    document = _load_table_tier_document()
    rules = document.setdefault("rules", [])
    rules_by_table = {
        (str(rule.get("schema")), str(rule.get("table"))): rule
        for rule in rules
        if isinstance(rule, dict) and rule.get("schema") and rule.get("table")
    }

    for row in rows:
        key = (str(row["schema_name"]), str(row["table_name"]))
        rule = rules_by_table.get(key)
        if rule is None:
            suggested_tier = _auto_classify_tier(row["table_name"])
            rule = {
                "id": f"{row['schema_name']}-{row['table_name']}-tier",
                "status": "confirmed" if suggested_tier else "need_check",
                "owner": "system" if suggested_tier else "data-owner",
                "schema": row["schema_name"],
                "table": row["table_name"],
                "tier": suggested_tier,
            }
            rules.append(rule)
            rules_by_table[key] = rule
            continue

        assigned_tier = _normalize_tier(rule.get("tier"))
        if assigned_tier is not None:
            rule["tier"] = assigned_tier
            rule["status"] = "confirmed"
            continue

        suggested_tier = _auto_classify_tier(row["table_name"])
        if suggested_tier:
            rule["status"] = "confirmed"
            rule["owner"] = "system"
            rule["tier"] = suggested_tier
        else:
            rule["status"] = "need_check"
            rule["tier"] = None

    _write_table_tier_document(document)
    return _table_tiers_path()


def check_table_tiers_complete(rows: list[dict[str, Any]]) -> bool:
    """Return whether every current table has an assigned Tier 1, 2 or 3."""
    document = _load_table_tier_document()
    rules = {
        (str(rule.get("schema")), str(rule.get("table"))): rule
        for rule in document.get("rules", [])
        if isinstance(rule, dict)
    }
    return all(
        _normalize_tier(
            rules.get((str(row["schema_name"]), str(row["table_name"])), {}).get(
                "tier"
            )
        )
        is not None
        for row in rows
    )


def classify_tables(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load the completed table-tier rules and classify the current tables."""
    tiers = load_table_tiers()
    return [
        {
            **row,
            "classification": tiers.get(
                (row["schema_name"], row["table_name"]), "need_check"
            ),
        }
        for row in rows
    ]
