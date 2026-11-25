"""Правила сверки пользователей без чтения файлов или изменения учётных записей."""

from collections import defaultdict


ACTIVE = "Активна"
DISABLED = "Неактивна"
UNKNOWN_STATUS = "Неизвестно"

REVIEW = "Проверить доступ"
UNKNOWN = "Недостаточно данных"
OK = "Расхождений нет"
INACTIVE = "Учётка неактивна"


def name_parts(value):
    if not isinstance(value, str):
        return ()
    return tuple(value.casefold().replace("ё", "е").split())


class NameIndex:
    """Сохраняет исходные ФИО и все варианты с одинаковыми фамилией и именем."""

    def __init__(self, names):
        self.names = {}
        self.by_pair = defaultdict(set)
        self.counts = defaultdict(int)
        for name in names:
            parts = name_parts(name)
            if parts:
                self.names.setdefault(parts, name.strip())
                self.counts[parts] += 1
                self.by_pair[parts[:2]].add(parts)


def _match(parts, sources, query_index):
    """Ищет подтверждение в источниках с учётом однофамильцев по обе стороны."""
    if len(parts) < 2:
        return [], "Недостаточно данных в ФИО"

    if query_index is not None and query_index.counts.get(parts, 0) > 1:
        return [], "ФИО повторяется в проверяемом списке"

    pair = parts[:2]
    if len(parts) > 2:
        exact = [label for label, idx in sources if idx.counts.get(parts) == 1]
        if exact:
            return exact, ""
        if any(parts in idx.names for _, idx in sources):
            return [], "Полное ФИО повторяется в источнике"

    # Для полного ФИО с другим отчеством совпадение возможно лишь с короткой записью.
    candidates = [
        (label, idx, candidate)
        for label, idx in sources
        for candidate in idx.by_pair.get(pair, ())
        if len(parts) == 2 or candidate == pair
    ]
    if not candidates:
        return [], ""

    variants = {parts}
    for _, idx in sources:
        variants.update(idx.by_pair.get(pair, ()))
    if query_index is not None:
        variants.update(query_index.by_pair.get(pair, ()))
    full_names = {variant for variant in variants if len(variant) > 2}
    repeated = any(idx.counts[candidate] > 1 for _, idx, candidate in candidates)
    competing_short = len(candidates) > 1 and any(
        candidate == pair for _, _, candidate in candidates
    )
    if len(full_names) > 1 or repeated or competing_short:
        return [], "Неоднозначное совпадение по ФИО"

    return list(dict.fromkeys(label for label, _, _ in candidates)), ""


def assess_account(name, status, roster_index, gph_index, service_index=None):
    """Оценивает учётку. None означает, что источник не удалось проверить."""
    references = [("Штатка", roster_index), ("ГПХ в AD", gph_index)]
    sources = [(label, idx) for label, idx in references if idx is not None]
    matched, problem = _match(name_parts(name), sources, service_index)
    unavailable = [label for label, idx in references if idx is None]
    if matched:
        basis = " + ".join(matched)
    elif problem:
        basis = problem
    elif unavailable:
        basis = "Источник недоступен: " + ", ".join(unavailable)
    else:
        basis = "Совпадений в штатке и ГПХ нет"

    if status not in (ACTIVE, DISABLED):
        status = UNKNOWN_STATUS
        result = UNKNOWN
    elif not matched and (problem or unavailable):
        result = UNKNOWN
    elif status == DISABLED:
        result = INACTIVE
    elif matched:
        result = OK
    else:
        result = REVIEW
    return {"Основание": basis, "Статус учётки": status, "Результат": result}


def compare_staff(roster_index, ad_staff_index):
    """Возвращает расхождения между штаткой и включёнными штатными в AD."""
    sources = [("Штатка", roster_index), ("Штатные в AD", ad_staff_index)]
    missing = [label for label, idx in sources if idx is None]
    if missing:
        return [{"ФИО": "", "Источник": ", ".join(missing), "Статус": "Источник недоступен"}]

    differences = []
    for (label, idx), other in ((sources[0], sources[1]), (sources[1], sources[0])):
        for parts, name in idx.names.items():
            matched, problem = _match(parts, [other], idx)
            if not matched:
                differences.append({
                    "ФИО": name, "Источник": label,
                    "Статус": problem or "Нет совпадения в источнике «" + other[0] + "»",
                })
    return differences
