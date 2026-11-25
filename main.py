# main.py
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from config import OUTPUT_FILE
from excel_processor import process_excel_data
from ad_export import export_ad_users

# Получаем логгер для этого модуля
logger = logging.getLogger(__name__)

def get_user_choice():
    """Получение выбора пользователя"""
    print("\n" + "="*50)
    print("Выберите опции для проверки (через пробел):")
    print("0 - Всё")
    print("1 - 1С")
    print("2 - Сфера Курьер")
    print("3 - Контур Диадок")
    print("="*50)
    
    while True:
        choice = input("Ваш выбор: ").strip()
        
        if not choice:
            print("Пожалуйста, введите хотя бы одну цифру")
            continue
            
        choices = choice.split()
        
        # Проверка на валидность ввода
        valid_choices = {'0', '1', '2', '3'}
        if all(c in valid_choices for c in choices):
            # Если выбран 0, добавляем все остальные опции
            if '0' in choices:
                return {0, 1, 2, 3}
            return set(int(c) for c in choices)
        else:
            print("Некорректный ввод. Пожалуйста, используйте цифры 0, 1, 2, 3 через пробел")

def get_employee_type_choice():
    """Получение выбора типа сотрудников"""
    print("\n" + "="*50)
    print("Выберите тип сотрудников для проверки (через пробел):")
    print("0 - Все")
    print("1 - Сотрудники")
    print("2 - ГПХ")
    print("="*50)
    
    while True:
        choice = input("Ваш выбор: ").strip()
        
        if not choice:
            print("Пожалуйста, введите хотя бы одну цифру")
            continue
            
        choices = choice.split()
        
        # Проверка на валидность ввода
        valid_choices = {'0', '1', '2'}
        if all(c in valid_choices for c in choices):
            # Если выбран 0, добавляем все остальные опции
            if '0' in choices:
                return {0, 1, 2}
            return set(int(c) for c in choices)
        else:
            print("Некорректный ввод. Пожалуйста, используйте цифры 0, 1, 2 через пробел")

def main():
    logger.info("Запуск обработки данных")
    
    # Получаем выбор пользователя
    selected_options = get_user_choice()
    selected_employee_types = get_employee_type_choice()
    logger.info(f"Выбранные опции: {selected_options}")
    logger.info(f"Выбранные типы сотрудников: {selected_employee_types}")
    
    with TemporaryDirectory() as directory:
        snapshot = Path(directory)
        ad_counts = None
        try:
            total, staff, contractors = export_ad_users(snapshot_dir=snapshot)
            ad_counts = (staff, contractors)
            logger.info("AD: всего %d, штатных %d, ГПХ %d", total, staff, contractors)
        except Exception:
            logger.exception("Экспорт AD недоступен. Продолжаем проверку по штатке")

        process_excel_data(selected_options, selected_employee_types,
                           ad_counts=ad_counts, snapshot_dir=snapshot)
    logger.info("Результаты сохранены в файл: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
