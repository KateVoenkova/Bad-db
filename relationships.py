from models import db, Character, CharacterRelationship
from collections import defaultdict
import re
from typing import Dict, List, Tuple


class RelationshipFinder:
    def __init__(self, book_id: int, window_size: int = 50):
        self.book_id = book_id
        self.window_size = window_size
        self.characters = self._load_characters()
        self.name_variations = self._generate_name_variations()

    def _load_characters(self) -> Dict[str, int]:
        """Загрузка персонажей с нормализованными именами"""
        characters = Character.query.filter_by(book_id=self.book_id).all()
        return {char.normalized_name: char.id for char in characters}

    def _generate_name_variations(self) -> Dict[str, str]:
        """Генерация вариантов имён для поиска в тексте"""
        variations = {}
        for name, char_id in self.characters.items():
            parts = name.split()
            # Полное имя
            variations[' '.join(parts)] = char_id
            # Только имя + фамилия
            if len(parts) > 2:
                variations[f"{parts[0]} {parts[-1]}"] = char_id
            # Только фамилия
            variations[parts[-1]] = char_id
        return variations

    def _find_mentions(self, text: str) -> List[Tuple[int, int]]:
        """Поиск упоминаний персонажей в тексте"""
        mentions = []
        words = re.findall(r'\b\w+\b', text.lower())

        for idx, word in enumerate(words):
            if word in self.name_variations:
                char_id = self.name_variations[word]
                mentions.append((idx, char_id))

        return mentions

    def _count_relationships(self, mentions: List[Tuple[int, int]]) -> Dict[Tuple[int, int], int]:
        """Подсчёт частоты совместных упоминаний"""
        relationships = defaultdict(int)

        for i in range(len(mentions)):
            for j in range(i + 1, min(i + self.window_size, len(mentions))):
                pos_i, char1 = mentions[i]
                pos_j, char2 = mentions[j]

                if char1 != char2:
                    key = tuple(sorted((char1, char2)))
                    relationships[key] += 1

        return relationships

    def process_text(self, text: str):
        """Основной метод обработки текста"""
        mentions = self._find_mentions(text)
        relationships = self._count_relationships(mentions)
        self._save_relationships(relationships)

    def _save_relationships(self, relationships: Dict[Tuple[int, int], int]):
        """Сохранение отношений в базу данных"""
        # Удаляем старые отношения
        CharacterRelationship.query.filter_by(book_id=self.book_id).delete()

        # Сохраняем новые
        for (char1_id, char2_id), weight in relationships.items():
            if weight >= 2:  # Сохраняем только значимые связи
                rel = CharacterRelationship(
                    character1_id=char1_id,
                    character2_id=char2_id,
                    book_id=self.book_id,
                    weight=weight
                )
                db.session.add(rel)

        db.session.commit()


def find_relationships(book_id: int, file_path: str):
    """Функция для обратной совместимости"""
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            encoding = chardet.detect(raw_data)['encoding']

        with open(file_path, 'r', encoding=encoding) as f:
            text = f.read()

        finder = RelationshipFinder(book_id)
        finder.process_text(text)
    except Exception as e:
        print(f"Error processing relationships: {str(e)}")
        raise