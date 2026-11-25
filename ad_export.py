"""Выгрузка полного списка AD и активных пользователей по категориям."""

import json
import logging
import re
from shutil import copyfile
import subprocess
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from config import AD_EXPORT_DIR, OUTPUT_DIR

logger = logging.getLogger('ad_export')
FIELDS = ('Name', 'SamAccountName', 'Enabled', 'EmailAddress', 'Company', 'DistinguishedName')
EXPORT_MARKER = 'AD_EXPORT_JSON:'

# Выводим результат только после успешного чтения всего каталога. Если запрос
# LDAP обрывается, его частичные записи не смешиваются с результатом Get-ADUser.
POWERSHELL_EXPORT = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ErrorActionPreference = 'Stop'
try {
    $root = [System.DirectoryServices.ActiveDirectory.Domain]::GetCurrentDomain().GetDirectoryEntry()
    $searcher = [System.DirectoryServices.DirectorySearcher]::new($root)
    $searcher.Filter = '(&(objectCategory=person)(objectClass=user))'
    $searcher.SearchScope = 'Subtree'
    $searcher.PageSize = 1000
    $searcher.PropertiesToLoad.AddRange([string[]]@(
        'name','samaccountname','useraccountcontrol','mail','company','distinguishedname'
    ))
    $results = $searcher.FindAll()
    $expected = $results.Count
    $users = @(foreach ($result in $results) {
        $p = $result.Properties
        if ($p['useraccountcontrol'].Count -ne 1) { throw 'Missing userAccountControl' }
        [pscustomobject]@{
            Name = [string]$p['name'][0]
            SamAccountName = [string]$p['samaccountname'][0]
            Enabled = (([int]$p['useraccountcontrol'][0] -band 2) -eq 0)
            EmailAddress = [string]($p['mail'] | Select-Object -First 1)
            Company = [string]($p['company'] | Select-Object -First 1)
            DistinguishedName = [string]$p['distinguishedname'][0]
        }
    })
} catch {
    $users = @(Get-ADUser -Filter * -Properties EmailAddress,Company |
        Select-Object Name,SamAccountName,Enabled,EmailAddress,Company,DistinguishedName)
    $expected = $users.Count
}
$json = ConvertTo-Json -InputObject @{ Count = $expected; Users = $users } -Depth 4 -Compress
[Console]::WriteLine('AD_EXPORT_JSON:' + $json)
"""


def clean_value(value):
    if value is None:
        return ''
    return ''.join(ch for ch in str(value) if unicodedata.category(ch)[0] != 'C').strip()


def _read_directory():
    process = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', POWERSHELL_EXPORT],
        capture_output=True,
    )
    if process.returncode:
        reason = process.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(f'Ошибка запроса AD (код {process.returncode}): {reason}')
    # Служебные сообщения PowerShell не являются частью результата.
    lines = process.stdout.decode('utf-8-sig').split('\n')
    payloads = [line[len(EXPORT_MARKER):] for line in lines if line.startswith(EXPORT_MARKER)]
    if len(payloads) != 1:
        raise ValueError('PowerShell не вернул полный снимок AD')
    snapshot = json.loads(payloads[0])
    users = snapshot['Users']
    if not isinstance(users, list) or not users or type(snapshot['Count']) is not int or snapshot['Count'] != len(users):
        raise ValueError('Число записей в выгрузке AD не подтверждено')

    for user in users:
        if not isinstance(user, dict) or not isinstance(user.get('Enabled'), bool):
            raise ValueError('В выгрузке AD отсутствует достоверный статус учётной записи')
        if not all(clean_value(user.get(field)) for field in ('Name', 'DistinguishedName')):
            raise ValueError('В выгрузке AD пропущено имя пользователя или его путь')
    return users


def export_ad_users(*, snapshot_dir=None):
    """Возвращает (всего, активные штатные, активные ГПХ) или сообщает об ошибке."""
    users = _read_directory()
    processed_users = []
    employees = []
    contractors = []
    for user in users:
        record = {field: clean_value(user.get(field)) for field in FIELDS}
        record['Enabled'] = 'Активна' if user['Enabled'] else 'Заблокирована'
        processed_users.append(record)
        if not user['Enabled']:
            continue

        # Экранированная запятая в имени не начинает новый компонент DN.
        parts = re.findall(r'(?:\\.|[^,])+', record['DistinguishedName'].casefold())
        ous = [part.strip()[3:] for part in parts if part.strip().startswith('ou=')]
        if 'external_organizations' in ous or any('гпх' in ou for ou in ous):
            contractors.append(record)
        elif 'cu_users' in ous:
            employees.append(record)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AD_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=OUTPUT_DIR) as temp_output, TemporaryDirectory(dir=AD_EXPORT_DIR) as temp_lists:
        full_text = Path(temp_output) / 'ad_users_export.txt'
        workbook = Path(temp_output) / 'ad_users_export.xlsx'
        with full_text.open('w', encoding='utf-8') as file:
            for record in processed_users:
                file.write('=' * 80 + '\n')
                for key, value in record.items():
                    file.write(f'{key}: {value}\n')
                file.write('\n')
        pd.DataFrame(processed_users, columns=FIELDS).to_excel(workbook, index=False, engine='openpyxl')

        lists = []
        for filename, records in [('сотрудники.txt', employees), ('ГПХ.txt', contractors)]:
            path = Path(temp_lists) / filename
            with path.open('w', encoding='utf-8') as file:
                for record in records:
                    file.write(f"Name: {record['Name']}\nStatus: {record['Enabled']}\n\n")
            lists.append(path)

        if snapshot_dir is not None:
            for path in lists:
                copyfile(path, Path(snapshot_dir) / path.name)

        for path in (full_text, workbook):
            path.replace(OUTPUT_DIR / path.name)
        for path in lists:
            path.replace(AD_EXPORT_DIR / path.name)

    counts = len(users), len(employees), len(contractors)
    logger.info('Экспорт AD завершён. Всего %d, штатных %d, ГПХ %d', *counts)
    return counts


if __name__ == '__main__':
    export_ad_users()
