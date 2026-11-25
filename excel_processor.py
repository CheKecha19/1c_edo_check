"""Сверка кадровых оснований и учётных записей прикладных систем."""

import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from assessment import (
    ACTIVE, DISABLED, REVIEW, UNKNOWN, UNKNOWN_STATUS,
    NameIndex, assess_account, compare_staff, name_parts,
)
from config import COMPARISON_SHEET, EMPLOYEES_FILE, GPH_FILE, OUTPUT_FILE, SHEET_NAME
from processors.onec_processor import load_onec_data_new_format
from utils import get_diadoc_data_source, get_kontur_data_source, load_shtat_data

logger = logging.getLogger(__name__)
RESULT_COLUMNS = ["Система", "ФИО", "Основание", "Статус учётки", "Результат"]


def read_ad_list(path, expected_count):
    """Проверяет, что список соответствует завершившемуся экспорту AD."""
    if expected_count is None:
        return None
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                 if line.strip()]
        if len(lines) != 2 * expected_count:
            raise ValueError("Число записей отличается от результата экспорта")
        names = []
        for name_line, status_line in zip(lines[::2], lines[1::2]):
            label, _, name = name_line.partition(":")
            if label != "Name" or not name.strip() or status_line != "Status: Активна":
                raise ValueError("Неполная запись или неожиданный статус AD")
            names.append(name.strip())
        return names
    except (OSError, UnicodeError, ValueError) as error:
        logger.warning("Список %s недоступен: %s", path.name, error)
        return None


def account_status(value):
    value = str(value).strip().casefold()
    if value in {"да", "активна", "true", "1"}:
        return ACTIVE
    if value in {"нет", "заблокирована", "false", "0"}:
        return DISABLED
    return UNKNOWN_STATUS


def evaluate_service(data, system, name_col, status_col, roster, contractors, categories):
    names = data[name_col].dropna()
    service_index = NameIndex(names)
    rows = []
    for name, raw_status in data[[name_col, status_col]].itertuples(index=False, name=None):
        if not name_parts(name):
            continue
        result = assess_account(name, account_status(raw_status), roster, contractors, service_index)
        # Неклассифицированная запись требует проверки при любом фильтре.
        if 0 not in categories and result["Результат"] != UNKNOWN:
            if result["Основание"] == "Штатка" and 1 not in categories:
                continue
            if result["Основание"] == "ГПХ в AD" and 2 not in categories:
                continue
        rows.append({"Система": system, "ФИО": name, **result})
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def process_excel_data(selected_options=None, employee_types=None, ad_counts=None, snapshot_dir=None):
    """Строит отчёт. Без подтверждения текущего экспорта данные AD недоступны."""
    selected = {0} if selected_options is None else selected_options
    categories = {0} if employee_types is None else employee_types
    staff_count, gph_count = ad_counts if ad_counts is not None else (None, None)
    staff_file = snapshot_dir / EMPLOYEES_FILE.name if snapshot_dir is not None else EMPLOYEES_FILE
    gph_file = snapshot_dir / GPH_FILE.name if snapshot_dir is not None else GPH_FILE
    ad_staff = read_ad_list(staff_file, staff_count)
    ad_gph = read_ad_list(gph_file, gph_count)
    staff = load_shtat_data()["Штатное_ФИО"].dropna()
    staff = staff[staff.map(lambda value: bool(name_parts(value)))].reset_index(drop=True)
    roster = NameIndex(staff) if len(staff) else None
    contractors = NameIndex(ad_gph) if ad_gph is not None else None
    differences = compare_staff(roster, NameIndex(ad_staff) if ad_staff is not None else None)
    source_status = []

    def record_source(label, values):
        source_status.append({
            "Источник": label,
            "Состояние": "Проверен" if values is not None else "Недостаточно данных",
            "Записей": len(values) if values is not None else None,
        })

    record_source("Штатка", staff if roster is not None else None)
    record_source("Штатные в AD", ad_staff)
    record_source("ГПХ в AD", ad_gph)
    raw = [pd.DataFrame({
        "Штатное_ФИО": staff,
        "AD_сотрудники": pd.Series(ad_staff or [], dtype=object),
        "AD_Статус_сотрудники": pd.Series(["Активна"] * len(ad_staff or []), dtype=object),
        "AD_ГПХ": pd.Series(ad_gph or [], dtype=object),
        "AD_Статус_ГПХ": pd.Series(["Активна"] * len(ad_gph or []), dtype=object),
    })]
    services = [
        (1, "1С", load_onec_data_new_format, "1C_ФИО", "1C_Активен"),
        (2, "Сфера Курьер", get_diadoc_data_source, "Сфера_Курьер_ФИО", "Сфера_Курьер_Активен"),
        (3, "Контур Диадок", get_kontur_data_source, "Контур_Диадок_ФИО", "Контур_Диадок_статус"),
    ]
    audits = []
    extra_sheets = {}
    for option, system, loader, name_col, status_col in services:
        if 0 not in selected and option not in selected:
            continue
        try:
            data = loader().reset_index(drop=True)
            data = data.loc[data[name_col].map(lambda value: bool(name_parts(value)))].reset_index(drop=True)
        except Exception:
            logger.exception("Не удалось прочитать данные системы %s", system)
            data = pd.DataFrame()
        record_source(system, data if not data.empty else None)
        if data.empty:
            continue
        raw.append(data)
        audit = evaluate_service(data, system, name_col, status_col, roster, contractors, categories)
        audits.append(audit)
        candidates = audit[audit["Результат"] == REVIEW]
        if not candidates.empty:
            extra_sheets[f"удалить из {system}"] = candidates
        pairs = data[name_col].map(lambda name: name_parts(name)[:2])
        duplicates = data[pairs.duplicated(keep=False)]
        if not duplicates.empty:
            extra_sheets[f"дубли в {system}"] = duplicates
        logger.info("%s: на проверку доступа %s, недостаточно данных %s",
                    system, len(candidates), (audit["Результат"] == UNKNOWN).sum())

    assessment = pd.concat(audits, ignore_index=True) if audits else pd.DataFrame(columns=RESULT_COLUMNS)
    sheets = {
        "Оценка доступа": assessment,
        "Источники": pd.DataFrame(source_status),
        SHEET_NAME: pd.concat(raw, axis=1),
        COMPARISON_SHEET: pd.DataFrame(differences, columns=["ФИО", "Источник", "Статус"]),
        **extra_sheets,
    }
    incomplete = assessment[assessment["Результат"] == UNKNOWN]
    if not incomplete.empty:
        sheets["Недостаточно данных"] = incomplete
    # Готовый отчёт появляется в папке только после успешной записи всех листов.
    with NamedTemporaryFile(dir=OUTPUT_FILE.parent, suffix=".xlsx", delete=False) as temp:
        temporary_path = Path(temp.name)
    try:
        with pd.ExcelWriter(temporary_path, engine="openpyxl") as writer:
            for title, frame in sheets.items():
                frame.to_excel(writer, sheet_name=title, index=False)
        temporary_path.replace(OUTPUT_FILE)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"comparison_count": sum(bool(row["ФИО"]) for row in differences)}
