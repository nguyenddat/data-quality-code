import json
from pathlib import Path

from core.logging import setup_dimension_logger, setup_step_logger
from core.settings import config
from data_quality.completeness.step3_col_null_count import load_column_null_count
from data_quality.consistency.step1_table_relationship import (
    load_table_relationships,
    step_config as step1_config,
)
from data_quality.consistency.step2_orphan_record import (
    load_orphan_records,
    step_config as step2_config,
)
from data_quality.consistency.step3_pattern_check import (
    check_pattern_rules_complete,
    load_confirmed_pattern_rules,
    load_pattern_violations,
    prepare_pattern_rules,
    step_config as step3_config,
)
from data_quality.consistency.step4_enum_check import (
    check_enum_rules_complete,
    load_confirmed_enum_rules,
    load_enum_violations,
    prepare_enum_rules,
    step_config as step4_config,
)
from data_quality.consistency.step5_cross_table_check import (
    check_cross_table_rules_complete,
    load_cross_table_violations,
    prepare_cross_table_rules,
    step_config as step5_config,
)
from utils.confirmed_business import check_dependencies
from utils.excel import (
    load_or_create_consistency_workbook,
    save_consistency_excel,
    setup_consistency_sheet,
)
from utils.formatter import parse_column_reference


dimension = "Consistency"
logger = setup_dimension_logger(dimension)
step_loggers = [setup_step_logger(dimension, step) for step in range(1, 6)]


def _load_cache_metadata() -> dict[str, dict]:
    cache_metadata = {}
    for cache_file in sorted(Path(config.cache_dir).glob("*.json")):
        with cache_file.open("r", encoding="utf-8") as file:
            cache_metadata[cache_file.stem] = json.load(file)
    return cache_metadata


def _load_column_rows(cache_metadata: dict[str, dict]) -> list[dict]:
    rows = []
    for schema in cache_metadata:
        rows.extend(load_column_null_count(schema))
    return rows


def _pattern_sheet_rows(
    results: list[dict], rules: list[dict]
) -> list[list]:
    rules_by_id = {rule["id"]: rule for rule in rules}
    rows = []
    for result in results:
        rule = rules_by_id[result["rule_id"]]
        pattern = result["pattern"]
        if pattern == "date_order":
            column_one = rule["start_column"]
            column_two = rule["end_column"]
            reason = (
                f"{result['violation_count']} record(s) có cột bắt đầu >= cột kết thúc"
                if result["violation_count"]
                else ""
            )
        elif pattern == "total_detail":
            column_one = f"{rule['total_table']}.{rule['total_column']}"
            column_two = f"{rule['detail_table']}.{rule['detail_column']}"
            reason = (
                f"{result['violation_count']} record(s) có tổng header khác tổng detail"
                if result["violation_count"]
                else ""
            )
        else:
            column_one = rule["status_column"]
            column_two = ""
            reason = (
                f"{result['violation_count']} entity có trạng thái mới nhất không thuộc "
                f"terminal_values {rule['terminal_values']}"
                if result["violation_count"]
                else ""
            )
        rows.append(
            [
                pattern,
                column_one,
                column_two,
                "FAILED" if result["violation_count"] else "PASS",
                reason,
            ]
        )
    return rows


def _enum_sheet_rows(results: list[dict]) -> list[list]:
    return [
        [
            result["column"],
            "Value NOT IN allowed_values",
            "FAILED" if result["invalid_count"] else "PASS",
            result["invalid_values"] if result["invalid_count"] else "",
        ]
        for result in results
    ]


def _cross_table_sheet_rows(results: list[dict]) -> list[list]:
    rows = []
    for result in results:
        source_schema, source_table, source_column = parse_column_reference(result["source"])
        target_schema, target_table, target_column = parse_column_reference(result["target"])
        rows.append(
            [
                f"{source_schema}.{source_table}",
                source_column,
                f"{target_schema}.{target_table}",
                target_column,
                result["violation_count"],
                result["violation_pct"],
            ]
        )
    return rows


def load_consistency() -> dict[str, list[dict]]:
    """Run Consistency steps in order and pass each step's output forward."""
    cache_metadata = _load_cache_metadata()
    if not cache_metadata:
        logger.info("No metadata cache found")
        return {}

    logger.info("Loaded metadata cache for %d schema(s)", len(cache_metadata))

    step_loggers[0].info("Loading confirmed Tier 1 table relationships")
    if not check_dependencies(step1_config["depend_business"]):
        step_loggers[0].info(
            "Step 1 dependencies are not ready; review confirmed_business/completeness/table_tiers.yaml"
        )
        return {}
    relationships = load_table_relationships()
    step_loggers[0].info("Loaded %d relationship column(s)", len(relationships))

    step_loggers[1].info("Checking orphan records from Step 1 relationships")
    if not check_dependencies(step2_config["depend_business"]):
        step_loggers[1].info("Step 2 dependencies are not ready")
        return {}
    orphan_records = load_orphan_records(relationships)
    step_loggers[1].info("Checked %d FK constraint(s)", len(orphan_records))

    column_rows = _load_column_rows(cache_metadata)

    step_loggers[2].info("Preparing pattern business rules")
    prepare_pattern_rules(column_rows)
    if not check_dependencies(step3_config["depend_business"]) or not check_pattern_rules_complete():
        step_loggers[2].info(
            "Step 3 rules are not ready; review confirmed_business/consistency/pattern_checks.yaml"
        )
        return {}
    pattern_rules = load_confirmed_pattern_rules()
    pattern_violations = load_pattern_violations(pattern_rules)
    step_loggers[2].info("Checked %d confirmed pattern rule(s)", len(pattern_violations))

    step_loggers[3].info("Preparing enum business rules")
    prepare_enum_rules(column_rows)
    if not check_dependencies(step4_config["depend_business"]) or not check_enum_rules_complete():
        step_loggers[3].info(
            "Step 4 rules are not ready; review confirmed_business/consistency/enum_checks.yaml"
        )
        return {}
    enum_rules = load_confirmed_enum_rules()
    enum_violations = load_enum_violations(enum_rules)
    step_loggers[3].info("Checked %d confirmed enum rule(s)", len(enum_violations))

    step_loggers[4].info("Preparing cross-table business rules")
    prepare_cross_table_rules(column_rows, relationships)
    if not check_dependencies(step5_config["depend_business"]) or not check_cross_table_rules_complete():
        step_loggers[4].info(
            "Step 5 rules are not ready; review confirmed_business/consistency/cross_table_checks.yaml"
        )
        return {}
    cross_table_violations = load_cross_table_violations()
    step_loggers[4].info(
        "Checked %d confirmed cross-table rule(s)", len(cross_table_violations)
    )

    workbook = load_or_create_consistency_workbook()
    setup_consistency_sheet(
        workbook,
        "Step 2 - Orphan",
        ["Bảng con", "Cột FK", "Bảng cha", "Số orphan", "Tỷ lệ orphan"],
        [
            [
                f"{result['child_schema']}.{result['child_table']}",
                result["fk_column"],
                f"{result['parent_schema']}.{result['parent_table']}",
                result["orphan_count"],
                result["orphan_pct"],
            ]
            for result in orphan_records
        ],
    )
    setup_consistency_sheet(
        workbook,
        "Step 3 - Pattern",
        ["Loại pattern", "Cột 1", "Cột 2", "Status", "Lý do failed"],
        _pattern_sheet_rows(pattern_violations, pattern_rules),
    )
    setup_consistency_sheet(
        workbook,
        "Step 4 - Enum",
        ["Cột kiểm tra", "Check", "Status", "Failed values"],
        _enum_sheet_rows(enum_violations),
    )
    setup_consistency_sheet(
        workbook,
        "Step 5 - Cross-table",
        ["Bảng nguồn", "Cột nguồn", "Bảng đích", "Cột đích", "Số record lệch", "Tỷ lệ"],
        _cross_table_sheet_rows(cross_table_violations),
    )
    result_file = save_consistency_excel(workbook)
    logger.info("Saved consistency results to %s", result_file)

    return {
        "relationships": relationships,
        "orphan_records": orphan_records,
        "pattern_violations": pattern_violations,
        "enum_violations": enum_violations,
        "cross_table_violations": cross_table_violations,
    }


if __name__ == "__main__":
    load_consistency()
