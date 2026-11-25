import requests
from requests.auth import HTTPBasicAuth
import json
import sys

def get_active_users(odata_url, username, password, resource_name, filter_query=None):
    """
    Выполняет запрос к OData сервису 1С для получения данных из указанного ресурса.
    
    :param odata_url: Базовый URL OData сервиса (например, http://server/база/odata/standard.odata)
    :param username: Имя пользователя для аутентификации
    :param password: Пароль
    :param resource_name: Имя ресурса (например, 'Catalog_Пользователи' или 'InformationRegister_АктивныеПользователи')
    :param filter_query: Строка фильтра OData (например, "Заблокирован eq false")
    :return: Список записей или None при ошибке
    """
    url = f"{odata_url.rstrip('/')}/{resource_name}"
    params = {}
    if filter_query:
        params['$filter'] = filter_query
    
    headers = {
        'Accept': 'application/json;odata.metadata=minimal',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(
            url,
            params=params,
            auth=HTTPBasicAuth(username, password),
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return data.get('value', [])
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при выполнении запроса: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Ошибка разбора JSON: {e}")
        return None

def main():
    # === НАСТРОЙКИ ПОДКЛЮЧЕНИЯ ===
    ODATA_URL = "http://localhost/база/odata/standard.odata"  # замените на ваш адрес
    USERNAME = "Администратор"                                 # логин
    PASSWORD = "password"                                      # пароль

    # === НАСТРОЙКИ ЗАПРОСА ===
    # Вариант 1: Регистр сведений "АктивныеПользователи" (если используется)
    # RESOURCE = "InformationRegister_АктивныеПользователи"
    # FILTER = None  # или можно добавить фильтр, если есть признак блокировки

    # Вариант 2: Справочник "Пользователи" с фильтром по не заблокированным
    RESOURCE = "Catalog_Пользователи"
    FILTER = "Заблокирован eq false"

    # === ВЫПОЛНЕНИЕ ===
    users = get_active_users(ODATA_URL, USERNAME, PASSWORD, RESOURCE, FILTER)
    
    if users is None:
        sys.exit(1)
    
    if not users:
        print("Нет данных о пользователях (возможно, неверное имя ресурса или фильтр).")
        sys.exit(0)
    
    print(f"Найдено записей: {len(users)}")
    for user in users:
        print(json.dumps(user, ensure_ascii=False, indent=2))
    
    # Пример вывода только имён пользователей:
    # for user in users:
    #     print(user.get('Наименование', user.get('Description', 'Без имени')))

if __name__ == "__main__":
    main()