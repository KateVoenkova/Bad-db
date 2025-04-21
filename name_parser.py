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
        except:
            normalized_parts.append(part)
    return ' '.join(normalized_parts)


def extract_all_names(text: str) -> List[str]:
    """Улучшенное извлечение имён с поддержкой русских и иностранных имён"""
    # Основной паттерн для русских имён (2-3 слова с заглавной буквы)
    name_pattern = re.compile(
        r'(?<!\w)([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})(?!\w)|'
        r'(?<!\w)([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})(?!\w)'
    )

    # Паттерн для титулов (князь, граф и т.д.)
    title_pattern = re.compile(
        r'(?<!\w)(граф|князь|генерал|лорд|сэр|мистер|мадемуазель)\s+([А-ЯЁA-Z][а-яёa-z]+)(?!\w)'
    )

    names = []

    # Находим обычные имена
    for match in name_pattern.finditer(text):
        name = match.group(1) or match.group(2)
        if name:
            names.append(name)

    # Находим имена с титулами
    for match in title_pattern.finditer(text):
        names.append(f"{match.group(1)} {match.group(2)}")

    # Фильтрация через морфологический анализатор
    filtered_names = []
    for name in names:
        parts = name.split()
        valid = True
        for part in parts:
            try:
                parsed = morph.parse(part)[0]
                if not any(tag in str(parsed.tag) for tag in ['Name', 'Surn', 'Geox']):
                    valid = False
                    break
            except:
                valid = False
                break
        if valid:
            filtered_names.append(name)

    # Нормализация и удаление дубликатов
    normalized_names = list(set(normalize_name(name) for name in filtered_names))

    return [name for name in normalized_names if len(name.split()) >= 2]  # Возвращаем только полные имена


def get_names_from_file(file_path: str) -> List[str]:
    """Чтение файла и извлечение имён с автоматическим определением кодировки"""
    with open(file_path, 'rb') as f:
        raw_data = f.read()
        encoding = chardet.detect(raw_data)['encoding']

    with open(file_path, 'r', encoding=encoding) as f:
        text = f.read()

    return extract_all_names(text)