import json
from pathlib import Path

from core.logging import setup_dimension_logger, setup_step_logger
from core.settings import config
from data_quality.completeness.step3_col_null_count import load_column_null_count
from data_quality.uniqueness.step2_technical_duplicate import (
    load_technical_duplicates,
    select_layer1_scope,
    step_config as step2_config,
)
from data_quality.uniqueness.step4_unique_constraint import (
    load_unique_constraints,
    load_unique_constraint_violations,
)
from utils.confirmed_business import check_dependencies
from utils.excel import (
    load_or_create_uniqueness_workbook,
    save_uniqueness_excel,
    setup_uniqueness_sheet,
)


dimension = "Uniqueness"
logger = setup_dimension_logger(dimension)
step_loggers = {
    step: setup_step_logger(dimension, step) for step in (1, 2, 4)
}


def _load_cache_metadata() -> dict[str, dict]:
    metadata = {}
    for cache_file in sorted(Path(config.cache_dir).glob("*.json")):
        with cache_file.open("r", encoding="utf-8") as file:
            metadata[cache_file.stem] = json.load(file)
    return metadata


def _load_column_rows(cache_metadata: dict[str, dict]) -> list[dict]:
    rows = []
    for schema in cache_metadata:
        rows.extend(load_column_null_count(schema))
    return rows


def load_uniqueness() -> dict[str, list[dict]]:
    """Run Uniqueness Step 1, Step 2 Layer 2, and Step 4."""
    cache_metadata = _load_cache_metadata()
    if not cache_metadata:
        logger.info("No metadata cache found")
        return {}

    column_rows = _load_column_rows(cache_metadata)
    step_loggers[1].info(
        "Selecting Tier 1 tables and excluding infrastructure tables in Step 2 Layer 1"
    )
    if not check_dependencies(step2_config["depend_business"]):
        step_loggers[1].info("Step 1 dependencies are not ready")
        return {}
    scope = select_layer1_scope(column_rows)
    step_loggers[1].info("Selected %d table(s) for the technical scan", len(scope))

    step_loggers[2].info("Checking technical duplicates across business columns")
    duplicates = load_technical_duplicates(column_rows)
    step_loggers[2].info("Checked %d table(s)", len(duplicates))

    step_loggers[4].info("Rechecking declared UNIQUE constraints")
    unique_constraint_results = load_unique_constraint_violations(
        load_unique_constraints(set(scope))
    )
    step_loggers[4].info(
        "Checked %d UNIQUE constraint(s)", len(unique_constraint_results)
    )

    workbook = load_or_create_uniqueness_workbook()
    setup_uniqueness_sheet(
        workbook,
        "Step 2 - Technical Duplicate",
        [
            "Schema",
            "Table",
            "Business Columns",
            "Checked Rows",
            "Duplicate Groups",
            "Affected Rows",
            "Duplicate %",
        ],
        [
            [
                result["schema_name"],
                result["table_name"],
                ", ".join(result["columns"]),
                result["checked_count"],
                result["duplicate_group_count"],
                result["duplicate_row_count"],
                result["duplicate_pct"],
            ]
            for result in duplicates
        ],
    )
    setup_uniqueness_sheet(
        workbook,
        "Step 4 - Unique Constraint",
        [
            "Schema",
            "Table",
            "Constraint",
            "Columns",
            "Checked Rows",
            "Duplicate Groups",
            "Affected Rows",
            "Duplicate %",
        ],
        [
            [
                result["schema_name"],
                result["table_name"],
                result["constraint_name"],
                ", ".join(result["columns"]),
                result["checked_count"],
                result["duplicate_group_count"],
                result["duplicate_row_count"],
                result["duplicate_pct"],
            ]
            for result in unique_constraint_results
        ],
    )
    result_file = save_uniqueness_excel(workbook)
    logger.info("Saved uniqueness results to %s", result_file)

    return {
        "technical_duplicates": duplicates,
        "unique_constraint_violations": unique_constraint_results,
    }


if __name__ == "__main__":
    load_uniqueness()
