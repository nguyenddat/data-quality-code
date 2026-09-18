"""Read confirmed business rules stored as YAML files."""

from pathlib import Path
from typing import Any

import yaml

from core.settings import config


_EMPTY_TABLE_TIERS = (
    "version: 1\n"
    "dimension: completeness\n"
    "step: table_tier\n"
    "description: Phân loại bảng nghiệp vụ theo Tier.\n"
    "status: draft\n"
    "rules: []\n"
)

def check_dependencies(dependencies: list[Path]) -> bool:
    for dependency in dependencies:
        dependency_path = Path(dependency)
        if not dependency_path.is_file():
            return False

        with dependency_path.open("r", encoding="utf-8") as file:
            document = yaml.safe_load(file)

        if not isinstance(document, dict) or not document:
            return False
        if str(document.get("status", "")).lower() == "draft":
            return False

        rules = document.get("rules")
        if rules is not None:
            if not isinstance(rules, list) or not rules:
                return False
            if not any(
                isinstance(rule, dict) and rule.get("status") == "confirmed"
                for rule in rules
            ):
                return False
    return True

def ensure_table_tiers_file() -> Path:
    """Create the draft table-tier file when business confirmation is absent."""
    file_path = Path(config.base_dir) / "confirmed_business/completeness/table_tiers.yaml"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.exists():
        content = file_path.read_text(encoding="utf-8")
        if yaml.safe_load(content) is not None:
            return file_path

    file_path.write_text(_EMPTY_TABLE_TIERS, encoding="utf-8")
    return file_path


def load_rules(relative_path: str) -> dict[str, Any]:
    """Load one YAML rule file below the configured business-rules directory."""
    business_dir = Path(config.base_dir) / "confirmed_business"
    file_path = (business_dir / relative_path).resolve()
    if business_dir.resolve() not in file_path.parents:
        raise ValueError("Business rule path must stay inside confirmed_business")
    if file_path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Business rule files must use YAML format")
    if not file_path.is_file():
        raise FileNotFoundError(f"Business rule file not found: {relative_path}")

    with file_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Business rule file must contain a YAML mapping: {relative_path}")
    return data


def load_table_tiers() -> dict[tuple[str, str], Any]:
    """Load confirmed table tiers keyed by ``(schema, table)``."""
    ensure_table_tiers_file()
    document = load_rules("completeness/table_tiers.yaml")
    tiers: dict[tuple[str, str], Any] = {}
    for rule in document.get("rules", []):
        if not isinstance(rule, dict) or rule.get("status") != "confirmed":
            continue
        schema = rule.get("schema")
        table = rule.get("table")
        tier = rule.get("tier")
        if not all((schema, table, tier)):
            raise ValueError(
                "Each confirmed table tier must contain schema, table and tier"
            )
        key = (str(schema), str(table))
        if key in tiers and tiers[key] != tier:
            raise ValueError(f"Conflicting tiers for {schema}.{table}")
        tiers[key] = tier
    return tiers
