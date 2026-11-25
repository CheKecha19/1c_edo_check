# processors/onec_processor.py
import pandas as pd
import os
import logging
from utils import get_onec_file, is_file_recent, find_duplicates, find_internal_duplicates, find_users_to_remove

logger = logging.getLogger(__name__)

ONEC_COLUMNS = ['1C_ФИО', '1C_Активен']


def _read_onec_report(file_path):
    """Найти поля на листе по заголовкам, сохраняя неизвестные статусы."""
    raw = pd.read_excel(file_path, sheet_name='Лист_1', header=None, keep_default_na=False)
    # Pandas возвращает значение горизонтально объединённых ячеек в левой колонке.
    header_row = None
    for position, row in enumerate(raw.itertuples(index=False, name=None)):
        labels = [str(value).strip().casefold() for value in row]
        if labels.count('пользователь') != 1:
            continue
        if 'недействителен' in labels:
            header_row = position, labels
            break
        if header_row is None:
            header_row = position, labels

    if header_row is None:
        raise ValueError("На листе Лист_1 не найден заголовок «Пользователь»")
    position, labels = header_row
    names = raw.iloc[position + 1:, labels.index('пользователь')].astype(str).str.strip()
    result = pd.DataFrame({'1C_ФИО': names})
    if labels.count('недействителен') == 1:
        invalid = raw.iloc[position + 1:, labels.index('недействителен')]
        result['1C_Активен'] = invalid.astype(str).str.strip().str.casefold().map(
            {'нет': 'Да', 'да': 'Нет'}
        ).fillna('Неизвестно')
    else:
        logger.warning("Не удалось определить колонку «Недействителен» в выгрузке 1С")
        result['1C_Активен'] = 'Неизвестно'
    return result.loc[names.ne('')].reset_index(drop=True)


def load_onec_data_new_format():
    """Прочитать актуальную выгрузку 1С и исключить служебные учётные записи."""
    try:
        onec_file = get_onec_file()
        if not onec_file or not is_file_recent(onec_file):
            logger.warning("Актуальный файл 1С не найден")
            return pd.DataFrame(columns=ONEC_COLUMNS)
        users = _read_onec_report(onec_file)
        service_accounts = users['1C_ФИО'].str.contains('сервис|robot|робот', case=False)
        result = users.loc[~service_accounts].reset_index(drop=True)
        logger.info("Загружено %d записей из 1С", len(result))
        return result
    except Exception:
        logger.exception("Ошибка при загрузке данных 1С")
        return pd.DataFrame(columns=ONEC_COLUMNS)


def process_onec_data(df, ad_employees_df, selected_options, employee_types):
    """Обработка данных из 1С"""
    if 1 not in selected_options and 0 not in selected_options:
        return df, {}
    
    logger.info("Обработка данных 1С...")
    
    # Проверяем наличие необходимых данных в AD
    if ad_employees_df.empty or 'AD_ФИО' not in ad_employees_df.columns:
        logger.warning("AD DataFrame пуст или не содержит столбец 'AD_ФИО'")
        return df, {
            'duplicates_ad_1c': 0,
            'internal_duplicates_1c': 0,
            'users_to_remove_1c': pd.DataFrame()
        }
    
    # Загружаем данные из 1С в новом формате
    onec_data = load_onec_data_new_format()
    
    if not onec_data.empty:
        logger.info(f"Загружено {len(onec_data)} записей из 1С")
        
        # Убедимся, что не превышаем MAX_ROWS
        onec_fio = onec_data['1C_ФИО'][:len(df)]
        onec_active = onec_data['1C_Активен'][:len(df)]  # ИСПРАВЛЕНО: '1C_Активen' -> '1C_Активен'
        
        df['1C_ФИО'] = pd.Series(onec_fio)
        df['1C_Активен'] = pd.Series(onec_active)
        
        # Логируем несколько примеров для проверки
        sample_users = onec_data.head(3)[['1C_ФИО', '1C_Активен']].values.tolist()
        logger.debug(f"Примеры пользователей из 1С: {sample_users}")
        
        # Статистика по активным/неактивным
        active_count = len(onec_data[onec_data['1C_Активен'] == 'Да'])
        inactive_count = len(onec_data[onec_data['1C_Активен'] == 'Нет'])
        logger.info(f"Статистика 1С: {active_count} активных, {inactive_count} неактивных")
    else:
        logger.warning("Данные из 1С не загружены или пусты")
    
    # Разделение на отдельные DataFrame
    onec_df = df[['1C_ФИО', '1C_Активен']].dropna(subset=['1C_ФИО'])
    
    # Инициализация результатов
    results = {
        'duplicates_ad_1c': 0,
        'internal_duplicates_1c': 0,
        'users_to_remove_1c': pd.DataFrame()
    }
    
    if not onec_df.empty:
        # Поиск дубликатов для 1С
        results['duplicates_ad_1c'] = len(find_duplicates(ad_employees_df, onec_df, 'AD_ФИО', '1C_ФИО'))
        results['internal_duplicates_1c'] = len(find_internal_duplicates(onec_df, '1C_ФИО'))
        results['users_to_remove_1c'] = find_users_to_remove(onec_df, ad_employees_df, ad_employees_df)
        
        logger.info(f"Найдено дубликатов между AD и 1С: {results['duplicates_ad_1c']}")
        logger.info(f"Найдено внутренних дубликатов в 1С: {results['internal_duplicates_1c']}")
        logger.info(f"Найдено пользователей для удаления из 1С: {len(results['users_to_remove_1c'])}")
    else:
        logger.warning("DataFrame 1С пуст, пропускаем поиск дубликатов")
    
    return df, results

def parse_1c_users_report(file_path):
    """Прочитать все записи 1С. Для неизвестного статуса is_active равен None."""
    try:
        users = _read_onec_report(file_path)
        statuses = {'Да': True, 'Нет': False}
        return [
            {'user_name': name, 'is_active': statuses.get(active)}
            for name, active in users.itertuples(index=False, name=None)
        ]
    except Exception:
        logger.exception("Не удалось прочитать выгрузку 1С")
        return []


def process_users_data(users_data):
    """Показать число активных, неактивных и неопределённых записей."""
    active = sum(user['is_active'] is True for user in users_data)
    inactive = sum(user['is_active'] is False for user in users_data)
    print(f"Всего пользователей: {len(users_data)}")
    print(f"Активных: {active}")
    print(f"Неактивных: {inactive}")
    print(f"Неизвестно: {len(users_data) - active - inactive}")
    labels = {True: 'Да', False: 'Нет', None: 'Неизвестно'}
    for user in users_data[:5]:
        print(f"Пользователь: {user['user_name']}, активен: {labels[user['is_active']]}")


# Использование
if __name__ == "__main__":
    file_path = "Users.1C.xlsx"  # Изменил имя файла на актуальное
    
    if os.path.exists(file_path):
        users_data = parse_1c_users_report(file_path)
        process_users_data(users_data)
    else:
        print("Файл не найден")