import re
from pathlib import Path
from typing import Any

import yaml

from core.settings import config
from core.database import get_connection
from utils.confirmed_business import load_rules
from utils.formatter import parse_table_reference, quote_identifier


step_config = {
    "step": 3,
    "dimension": "consistency",
    "depend_business": [],
}

PATTERN_RULES_FILE = "consistency/pattern_checks.yaml"
DATE_TYPES = {
    "date",
    "timestamp without time zone",
    "timestamp with time zone",
}
DATE_START_TOKENS = ("start", "begin", "from", "vao", "bat_dau", "tu")
DATE_END_TOKENS = ("end", "finish", "to", "ra", "ket_thuc", "den")
STATUS_TOKENS = ("status", "state", "trang_thai", "tinh_trang")


def _rules_path() -> Path:
    return Path(config.base_dir) / "confirmed_business" / PATTERN_RULES_FILE


def _load_pattern_document() -> dict[str, Any]:
    path = _rules_path()
    if not path.is_file():
        return {"version": 1, "dimension": "consistency", "step": "pattern_check", "status": "draft", "rules": []}
    with path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}
    if not isinstance(document, dict) or not isinstance(document.get("rules", []), list):
        raise ValueError("pattern_checks.yaml must contain a mapping with a rules list")
    return document


def _write_pattern_document(document: dict[str, Any]) -> None:
    path = _rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(document, file, allow_unicode=True, sort_keys=False)


def _is_date_column(row: dict[str, Any]) -> bool:
    return str(row.get("data_type", "")).casefold() in DATE_TYPES


def _contains_token(column_name: str, tokens: tuple[str, ...]) -> bool:
    name = column_name.casefold()
    return any(re.search(rf"(^|_){re.escape(token)}($|_)", name) for token in tokens)


def _auto_detect_date_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if _is_date_column(row):
            grouped.setdefault((str(row["schema_name"]), str(row["table_name"])), []).append(row)

    rules = []
    for (schema, table), columns in grouped.items():
        starts = [row for row in columns if _contains_token(row["column_name"], DATE_START_TOKENS)]
        ends = [row for row in columns if _contains_token(row["column_name"], DATE_END_TOKENS)]
        for start in starts:
            for end in ends:
                rules.append(
                    {
                        "id": f"auto-{schema}-{table}-{start['column_name']}-{end['column_name']}-date-order",
                        "status": "need_check",
                        "owner": "data-owner",
                        "pattern": "date_order",
                        "table": f"{schema}.{table}",
                        "start_column": start["column_name"],
                        "end_column": end["column_name"],
                        "operator": "<",
                        "note": "Data owner xác nhận start_column và end_column.",
                    }
                )
    return rules


def _auto_detect_status_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = []
    for row in rows:
        column = str(row["column_name"])
        if not _contains_token(column, STATUS_TOKENS):
            continue
        schema = str(row["schema_name"])
        table = str(row["table_name"])
        rules.append(
            {
                "id": f"auto-{schema}-{table}-{column}-status",
                "status": "need_check",
                "owner": "data-owner",
                "pattern": "status_terminal",
                "table": f"{schema}.{table}",
                "status_column": column,
                "terminal_values": [],
                "entity_columns": [],
                "event_time_column": None,
                "require_terminal": None,
                "note": "Data owner bổ sung entity_columns, event_time_column, terminal_values và require_terminal.",
            }
        )
    return rules


def prepare_pattern_rules(rows: list[dict[str, Any]]) -> Path:
    """Create/update auto-detected rules and a business-check placeholder."""
    document = _load_pattern_document()
    rules = document.setdefault("rules", [])
    existing_ids = {rule.get("id") for rule in rules if isinstance(rule, dict)}

    for rule in _auto_detect_date_rules(rows) + _auto_detect_status_rules(rows):
        if rule["id"] not in existing_ids:
            rules.append(rule)
            existing_ids.add(rule["id"])

    if "business-total-detail" not in existing_ids:
        rules.append(
            {
                "id": "business-total-detail",
                "status": "need_check",
                "owner": "data-owner",
                "pattern": "total_detail",
                "note": "Xác nhận bảng header/line-item, cột tổng và công thức tính.",
            }
        )

    document["status"] = "confirmed" if any(
        isinstance(rule, dict) and rule.get("status") == "confirmed" for rule in rules
    ) else "draft"
    _write_pattern_document(document)
    return _rules_path()


def check_pattern_rules_complete() -> bool:
    """Return whether all prepared pattern rules have been confirmed."""
    document = _load_pattern_document()
    return all(
        isinstance(rule, dict) and rule.get("status") == "confirmed"
        for rule in document.get("rules", [])
    )


def load_confirmed_pattern_rules() -> list[dict[str, Any]]:
    """Load only confirmed pattern rules for the checking query layer."""
    return [
        rule
        for rule in load_rules(PATTERN_RULES_FILE).get("rules", [])
        if isinstance(rule, dict) and rule.get("status") == "confirmed"
    ]


def _required_rule_fields(rule: dict[str, Any]) -> tuple[str, ...]:
    pattern = rule.get("pattern")
    if pattern == "date_order":
        return ("table", "start_column", "end_column")
    if pattern == "total_detail":
        return (
            "total_table",
            "total_column",
            "detail_table",
            "detail_column",
            "join_columns",
        )
    if pattern == "status_terminal":
        return (
            "table",
            "status_column",
            "entity_columns",
            "event_time_column",
            "terminal_values",
            "require_terminal",
        )
    return ()


def _validate_rule(rule: dict[str, Any]) -> None:
    if not rule.get("id") or not rule.get("pattern"):
        raise ValueError("Each confirmed pattern rule must contain id and pattern")
    missing = [field for field in _required_rule_fields(rule) if field not in rule]
    if missing:
        raise ValueError(f"Pattern rule {rule['id']} is missing: {', '.join(missing)}")
    for field in ("table", "total_table", "detail_table"):
        if field in rule and "." in str(rule[field]):
            parse_table_reference(rule[field])
    if rule["pattern"] in {"date_order", "status_terminal"}:
        if "table" not in rule and not all(
            rule.get(field) for field in ("schema", "table")
        ):
            raise ValueError(f"Pattern rule {rule['id']} must define table as schema.table")
    if rule["pattern"] == "total_detail":
        for compact, schema, table in (
            ("total_table", "total_schema", "total_table"),
            ("detail_table", "detail_schema", "detail_table"),
        ):
            if compact not in rule and not all(rule.get(field) for field in (schema, table)):
                raise ValueError(
                    f"Pattern rule {rule['id']} must define {compact} as schema.table"
                )
    if rule["pattern"] == "total_detail":
        if not isinstance(rule["join_columns"], list) or not rule["join_columns"]:
            raise ValueError(f"Pattern rule {rule['id']} must define join_columns")
        if not all(
            isinstance(pair, dict)
            and pair.get("total")
            and pair.get("detail")
            for pair in rule["join_columns"]
        ):
            raise ValueError(f"Pattern rule {rule['id']} has invalid join_columns")
    if rule["pattern"] == "status_terminal":
        if not isinstance(rule["entity_columns"], list) or not rule["entity_columns"]:
            raise ValueError(f"Pattern rule {rule['id']} must define entity_columns")
        if not isinstance(rule["terminal_values"], list) or not rule["terminal_values"]:
            raise ValueError(f"Pattern rule {rule['id']} must define terminal_values")
        if not isinstance(rule["require_terminal"], bool):
            raise ValueError(f"Pattern rule {rule['id']} must define require_terminal as boolean")


def _qualified_table(schema: str, table: str) -> str:
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _table_parts(rule: dict[str, Any], compact_key: str, schema_key: str, table_key: str) -> tuple[str, str]:
    if compact_key in rule and "." in str(rule[compact_key]):
        return parse_table_reference(rule[compact_key])
    return rule[schema_key], rule[table_key]


def _build_date_order_query(rule: dict[str, Any]) -> str:
    schema, table_name = _table_parts(rule, "table", "schema", "table")
    table = _qualified_table(schema, table_name)
    start = quote_identifier(rule["start_column"])
    end = quote_identifier(rule["end_column"])
    return f"""
SELECT
    COUNT(*) AS checked_count,
    COALESCE(SUM(CASE WHEN {start} >= {end} THEN 1 ELSE 0 END), 0)
        AS violation_count
FROM {table}
WHERE {start} IS NOT NULL AND {end} IS NOT NULL;
"""


def _build_total_detail_query(rule: dict[str, Any]) -> str:
    total_schema, total_table_name = _table_parts(
        rule, "total_table", "total_schema", "total_table"
    )
    detail_schema, detail_table_name = _table_parts(
        rule, "detail_table", "detail_schema", "detail_table"
    )
    total_table = _qualified_table(total_schema, total_table_name)
    detail_table = _qualified_table(detail_schema, detail_table_name)
    total_alias = "total_record"
    detail_alias = "detail_record"
    join_columns = rule["join_columns"]
    detail_join = " AND ".join(
        f"detail_sums.{quote_identifier(pair['detail'])} = "
        f"{total_alias}.{quote_identifier(pair['total'])}"
        for pair in join_columns
    )
    total_column = f"{total_alias}.{quote_identifier(rule['total_column'])}"
    detail_column = quote_identifier(rule["detail_column"])
    tolerance = rule.get("tolerance", 0)
    if not isinstance(tolerance, (int, float)):
        raise ValueError(f"Pattern rule {rule['id']} tolerance must be numeric")
    return f"""
WITH detail_sums AS (
    SELECT
        {', '.join(f'{detail_alias}.{quote_identifier(pair["detail"])}' for pair in join_columns)},
        COALESCE(SUM({detail_alias}.{detail_column}), 0) AS detail_value
    FROM {detail_table} AS {detail_alias}
    GROUP BY {', '.join(f'{detail_alias}.{quote_identifier(pair["detail"])}' for pair in join_columns)}
)
SELECT
    COUNT(*) AS checked_count,
    COALESCE(SUM(
        CASE WHEN ABS(
            COALESCE({total_column}, 0) - COALESCE(detail_sums.detail_value, 0)
        ) > {tolerance} THEN 1 ELSE 0 END
    ), 0) AS violation_count
FROM {total_table} AS {total_alias}
LEFT JOIN detail_sums
  ON {detail_join}
WHERE {total_column} IS NOT NULL;
"""


def _build_status_terminal_query(rule: dict[str, Any]) -> str:
    schema, table_name = _table_parts(rule, "table", "schema", "table")
    table = _qualified_table(schema, table_name)
    entity_columns = [quote_identifier(column) for column in rule["entity_columns"]]
    partition = ", ".join(entity_columns)
    status_column = quote_identifier(rule["status_column"])
    event_time_column = quote_identifier(rule["event_time_column"])
    terminal_values = ", ".join(
        "'" + str(value).replace("'", "''") + "'"
        for value in rule["terminal_values"]
    )
    terminal_condition = f"latest_record.{status_column} NOT IN ({terminal_values})"
    return f"""
WITH ranked_records AS (
    SELECT
        {status_column},
        ROW_NUMBER() OVER (
            PARTITION BY {partition}
            ORDER BY {event_time_column} DESC NULLS LAST
        ) AS row_number
    FROM {table}
    WHERE {status_column} IS NOT NULL
      AND {event_time_column} IS NOT NULL
)
SELECT
    COUNT(*) AS checked_count,
    COALESCE(SUM(
        CASE WHEN {str(rule['require_terminal']).upper()} AND {terminal_condition}
             THEN 1 ELSE 0 END
    ), 0) AS violation_count
FROM ranked_records AS latest_record
WHERE latest_record.row_number = 1;
"""


def _build_pattern_query(rule: dict[str, Any]) -> str:
    _validate_rule(rule)
    pattern = rule["pattern"]
    if pattern == "date_order":
        return _build_date_order_query(rule)
    if pattern == "total_detail":
        return _build_total_detail_query(rule)
    if pattern == "status_terminal":
        return _build_status_terminal_query(rule)
    raise ValueError(f"Unsupported pattern: {pattern}")


def _make_pattern_result(
    rule: dict[str, Any], checked_count: int, violation_count: int
) -> dict[str, Any]:
    violation_pct = round((violation_count / checked_count) * 100, 2) if checked_count else 0.0
    return {
        "rule_id": rule["id"],
        "pattern": rule["pattern"],
        "checked_count": checked_count,
        "violation_count": violation_count,
        "violation_pct": violation_pct,
    }


def load_pattern_violations(
    rules: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Measure violations for confirmed pattern rules using read-only queries."""
    selected_rules = load_confirmed_pattern_rules() if rules is None else rules
    if not selected_rules:
        return []

    results = []
    with get_connection(read_only=True) as connection:
        cursor = connection.cursor()
        try:
            for rule in selected_rules:
                cursor.execute(_build_pattern_query(rule))
                checked_count, violation_count = cursor.fetchone()
                results.append(
                    _make_pattern_result(
                        rule,
                        int(checked_count or 0),
                        int(violation_count or 0),
                    )
                )
        finally:
            cursor.close()
    return results
