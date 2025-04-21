import re
from pymorphy2 import MorphAnalyzer
from typing import List

morph = MorphAnalyzer()

def normalize_name(name: str) -> str:
    """Приводит имя к начальной форме (именительный падеж)"""
    parts = name.split()
    normalized_parts = []
    for part in parts:
        try:
            parsed = morph.parse(part)[0]
            normalized_parts.append(parsed.normal_form.title())
        except Exception:
            normalized_parts.append(part)
    return ' '.join(normalized_parts)

def extract_all_names(text: str) -> List[str]:
    """Извлечение имён с поддержкой русских и англоязычных конструкций"""
    if not text:
        return []

    # Паттерн для обычных имён (двойных или тройных)
    name_pattern = re.compile(
        r'(?<!\w)([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})(?!\w)|'
        r'(?<!\w)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})(?!\w)'
    )

    # Паттерн для титулов (типа "граф Дракула")
    title_pattern = re.compile(
        r'(?<!\w)(граф|князь|генерал|барон|сэр|мистер|мисс|мадемуазель|лорд)\s+([А-ЯЁA-Z][а-яёa-z]+)(?!\w)'
    )

    raw_names = []

    # Поиск обычных имён
    for match in name_pattern.finditer(text):
        name = match.group(1) or match.group(2)
        if name:
            raw_names.append(name.strip())

    # Поиск титулованных имён
    for match in title_pattern.finditer(text):
        full_name = f"{match.group(1).capitalize()} {match.group(2)}"
        raw_names.append(full_name.strip())

    # Морфологическая фильтрация: только имена/фамилии/географические объекты
    filtered = []
    for name in raw_names:
        parts = name.split()
        valid = True
        for part in parts:
            try:
                parsed = morph.parse(part)[0]
                if not any(tag in parsed.tag for tag in ['Name', 'Surn', 'Geox']):
                    valid = False
                    break
            except Exception:
                valid = False
                break
        if valid:
            filtered.append(name)

    # Нормализация и удаление дубликатов
    normalized = set()
    for name in filtered:
        norm = normalize_name(name)
        if len(norm.split()) >= 2:  # только имена с 2+ словами
            normalized.add(norm)

    return sorted(normalized)

def get_names_from_file(file_path: str) -> List[str]:
    """Извлечение имён прямо из файла (в обход базы) — опциональный метод"""
    import chardet
    with open(file_path, 'rb') as f:
        raw_data = f.read()
        encoding = chardet.detect(raw_data)['encoding']

    with open(file_path, 'r', encoding=encoding) as f:
        text = f.read()

    return extract_all_names(text)
