import json
from pathlib import Path

from core.settings import config
from core.logging import setup_dimension_logger, setup_step_logger

from data_quality.completeness.step1_table_row_count import load_table_row_count, step_config as step1_config 
from data_quality.completeness.step2_table_classification import (
    check_table_tiers_complete,
    classify_tables,
    prepare_table_tiers,
    step_config as step2_config,
)
from data_quality.completeness.step3_col_null_count import load_column_null_count, step_config as step3_config
from data_quality.completeness.step4_col_null_classification import (
    check_column_null_rules_complete,
    classify_columns,
    prepare_column_null_rules,
    step_config as step4_config,
)
from data_quality.completeness.step5_dirty_value_check import load_dirty_value_check, step_config as step5_config

from utils.confirmed_business import check_dependencies
from utils.excel import save_excel, setup_column_sheet, setup_table_sheet

dimension = "Completeness"
logger = setup_dimension_logger(dimension)
step_loggers = [setup_step_logger(dimension, step) for step in range(1, 6)]


def _null_warning(row: dict) -> str:
    """Classify a column's NULL percentage using its table tier."""
    null_pct = row.get("null_pct")
    tier = str(row.get("table_tier", ""))
    if null_pct is None:
        return "🟢 Bình thường"
    if tier == "1" and null_pct > 5:
        return "🔴 Nguy hiểm"
    if tier in {"2", "3"} and null_pct > 90:
        return "🟡 Nghi ngờ"
    return "🟢 Bình thường"

def load_completeness() -> dict[str, dict]:
    table_sheet_data = []
    column_sheet_data = []

    # 0. Load cache metadata
    cache_metadata = {}
    for cache_file in sorted(Path(config.cache_dir).glob("*.json")):
        with cache_file.open("r", encoding="utf-8") as file:
            cache_metadata[cache_file.stem] = json.load(file)

    if not cache_metadata:
        logger.info("No metadata cache found")
        return {}
    
    logger.info("Loaded metadata cache for %d schema(s)", len(cache_metadata))

    # 1. Table Row Count
    step_loggers[0].info("Loading table row count")
    step_dependencies = step1_config["depend_business"]
    if not check_dependencies(step_dependencies):
        step_loggers[0].info("Step 1 dependencies are not ready")
        return {}

    for schema in cache_metadata:
        table_sheet_data.extend(load_table_row_count(schema))

    step_loggers[0].info("Loaded row count for %d table(s)", len(table_sheet_data))

    # 2. Table Tier Classify
    step_loggers[1].info("Classifying tables by tier")
    prepare_table_tiers(table_sheet_data)
    step_dependencies = step2_config["depend_business"]
    if not check_dependencies(step_dependencies) or not check_table_tiers_complete(table_sheet_data):
        step_loggers[1].info("Step 2 dependencies are not ready; review need_check tables in table_tiers.yaml")
        return {}

    table_sheet_data = classify_tables(table_sheet_data)
    step_loggers[1].info("Classified %d table(s)", len(table_sheet_data))

    # 3. Col Null Count
    step_loggers[2].info("Loading column NULL counts")
    step_dependencies = step3_config["depend_business"]
    if not check_dependencies(step_dependencies):
        step_loggers[2].info("Step 3 dependencies are not ready")
        return {}

    for schema in cache_metadata:
        column_sheet_data.extend(load_column_null_count(schema))

    step_loggers[2].info("Loaded NULL counts for %d column(s)", len(column_sheet_data))

    table_tiers = {
        (row["schema_name"], row["table_name"]): row.get("classification")
        for row in table_sheet_data
    }
    for row in column_sheet_data:
        row["table_tier"] = table_tiers.get(
            (row["schema_name"], row["table_name"])
        )
        row["null_warning"] = _null_warning(row)

    # 4. Col Null Classify
    step_loggers[3].info("Classifying column NULLs")
    prepare_column_null_rules(column_sheet_data)
    step_dependencies = step4_config["depend_business"]
    if not check_dependencies(step_dependencies) or not check_column_null_rules_complete(
        column_sheet_data
    ):
        step_loggers[3].info(
            "Step 4 dependencies are not ready; review need_check columns in "
            "confirmed_business/completeness/col_null_classified.yaml"
        )
        return {}

    column_sheet_data = classify_columns(column_sheet_data)
    step_loggers[3].info("Classified %d column(s)", len(column_sheet_data))

    # 5. Dirty Value Check
    step_loggers[4].info("Checking dirty values")
    step_dependencies = step5_config["depend_business"]
    if not check_dependencies(step_dependencies):
        step_loggers[4].info("Step 5 dependencies are not ready")
        return {}

    dirty_checks = []
    for schema in cache_metadata:
        dirty_checks.extend(load_dirty_value_check(schema))

    dirty_checks_by_column = {
        (item["schema_name"], item["table_name"], item["column_name"]): item[
            "dirty_value_check"
        ]
        for item in dirty_checks
    }
    for row in column_sheet_data:
        key = (row["schema_name"], row["table_name"], row["column_name"])
        row["dirty_value_check"] = dirty_checks_by_column.get(key, "Pass")

    step_loggers[4].info("Checked dirty values for %d column(s)", len(dirty_checks))

    # Lưu vào workbook
    workbook, table_sheet = setup_table_sheet()
    column_sheet = setup_column_sheet(workbook)

    for row_number in range(2, table_sheet.max_row + 1):
        for column_number in range(1, 12):
            table_sheet.cell(row=row_number, column=column_number).value = None
    for row_number in range(2, column_sheet.max_row + 1):
        for column_number in range(1, 19):
            column_sheet.cell(row=row_number, column=column_number).value = None

    for row_number, row in enumerate(table_sheet_data, start=2):
        values = [
            row["schema_name"],
            row["table_name"],
            row.get("classification", ""),
            row.get("comment", ""),
            row.get("row_count"),
            row.get("columns"),
            row.get("total_size"),
            row.get("data_size"),
            row.get("index_size"),
            row.get("has_pk"),
            row.get("fk_count"),
        ]
        for column_number, value in enumerate(values, start=1):
            table_sheet.cell(row=row_number, column=column_number, value=value)

    for row_number, row in enumerate(column_sheet_data, start=2):
        values = [
            row["schema_name"],
            row["table_name"],
            row["column_name"],
            row.get("data_type", ""),
            row.get("full_type", ""),
            row.get("nullable", False),
            row.get("default"),
            row.get("pk", False),
            row.get("fk", False),
            row.get("uq", False),
            row.get("pii_phi", ""),
            row.get("null_pct"),
            row.get("distinct_count"),
            row.get("comment", ""),
            row.get("null_warning", "🟢 Bình thường"),
            row.get("dirty_value_check", "Pass"),
            row.get("not_nullable", ""),
            row.get("null_classify", ""),
        ]
        for column_number, value in enumerate(values, start=1):
            column_sheet.cell(row=row_number, column=column_number, value=value)

    table_sheet.auto_filter.ref = f"A1:K{max(table_sheet.max_row, 1)}"
    column_sheet.auto_filter.ref = f"A1:R{max(column_sheet.max_row, 1)}"
    result_file = save_excel(workbook)
    logger.info("Saved completeness results to %s", result_file)
    return cache_metadata

if __name__ == "__main__":
    load_completeness()
