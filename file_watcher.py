import os
import re
import zipfile
import logging
from bs4 import BeautifulSoup
from ebooklib import epub
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from flask import current_app
from models import db, Country, Author, Book
from tasks import process_uploaded_file

logger = logging.getLogger(__name__)


class UploadHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.processed_files = set()

    def extract_metadata(self, filename):
        """Извлечение метаданных из имени файла с более гибкой логикой"""
        try:
            # Удаляем расширение и служебные символы
            clean_name = os.path.splitext(filename)[0]
            clean_name = re.sub(r'[\[\]\(\)\{\}_]', ' ', clean_name)
            clean_name = re.sub(r'\s+', ' ', clean_name).strip()

            # Попробуем извлечь автора и название
            patterns = [
                r'^(.*?)[\s\-_]+([\w\s]+)$',  # Автор - Название
                r'^([^0-9]+)(\d+.*)?$',  # АвторНомер
                r'^(\w+)\s*,\s*(\w+.*)$'  # Фамилия, Имя - Название
            ]

            for pattern in patterns:
                match = re.match(pattern, clean_name)
                if match:
                    author = match.group(1).strip()
                    title = match.group(2).strip() if match.group(2) else "Без названия"
                    return author, title

            # Если не удалось разобрать, возвращаем имя файла как название
            return "Неизвестный автор", clean_name
        except Exception as e:
            logger.error(f"Error extracting metadata from filename: {str(e)}")
            return "Неизвестный автор", "Без названия"

    def extract_metadata_from_file(self, filepath, file_format):
        """Извлечение метаданных из содержимого файла"""
        try:
            if file_format == 'epub':
                book = epub.read_epub(filepath)
                title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else None
                creator = book.get_metadata('DC', 'creator')[0][0] if book.get_metadata('DC', 'creator') else None
                return creator or "Неизвестный автор", title or "Без названия"

            elif file_format == 'fb2':
                with open(filepath, 'r', encoding='utf-8') as f:
                    soup = BeautifulSoup(f.read(), 'xml')
                    title = soup.find('book-title').text if soup.find('book-title') else None
                    first_name = soup.find('first-name').text if soup.find('first-name') else ''
                    last_name = soup.find('last-name').text if soup.find('last-name') else ''
                    author = f"{first_name} {last_name}".strip() if first_name or last_name else None
                    return author or "Неизвестный автор", title or "Без названия"

            elif file_format == 'zip':
                # Обработка ZIP-архивов (предполагаем, что внутри FB2)
                with zipfile.ZipFile(filepath, 'r') as z:
                    for name in z.namelist():
                        if name.lower().endswith('.fb2'):
                            with z.open(name) as f:
                                soup = BeautifulSoup(f.read(), 'xml')
                                title = soup.find('book-title').text if soup.find('book-title') else None
                                first_name = soup.find('first-name').text if soup.find('first-name') else ''
                                last_name = soup.find('last-name').text if soup.find('last-name') else ''
                                author = f"{first_name} {last_name}".strip() if first_name or last_name else None
                                return author or "Неизвестный автор", title or "Без названия"

            return None, None
        except Exception as e:
            logger.error(f"Error extracting metadata from file {filepath}: {str(e)}")
            return None, None

    def process_file(self, filepath):
        """Обработка нового или изменённого файла"""
        try:
            if filepath in self.processed_files:
                return

            self.processed_files.add(filepath)

            parts = filepath.split(os.sep)
            if len(parts) < 3:  # uploads/country/filename
                return

            country_name, filename = parts[-2], parts[-1]
            ext = os.path.splitext(filename)[1][1:].lower()

            if ext not in current_app.config['ALLOWED_EXTENSIONS']:
                return

            # Извлекаем метаданные из имени файла
            author_name, title = self.extract_metadata(filename)

            # Пробуем извлечь метаданные из содержимого файла
            content_author, content_title = self.extract_metadata_from_file(filepath, ext)
            if content_title and content_title != "Без названия":
                title = content_title
            if content_author and content_author != "Неизвестный автор":
                author_name = content_author

            with current_app.app_context():
                # Проверяем существование книги
                existing = Book.query.filter_by(file_path=filepath).first()
                if existing:
                    return

                try:
                    # Создаем или находим страну
                    country = Country.query.filter_by(normalized_name=country_name.lower()).first()
                    if not country:
                        country = Country(name=country_name)
                        db.session.add(country)
                        db.session.commit()

                    # Создаем автора (с обработкой ошибок валидации)
                    try:
                        author = Author.query.filter_by(normalized_name=author_name.lower()).first()
                        if not author:
                            author = Author(name=author_name, country_id=country.id)
                            db.session.add(author)
                            db.session.commit()
                    except ValueError as e:
                        logger.error(f"Error creating author {author_name}: {str(e)}")
                        author = Author.query.filter_by(normalized_name="неизвестный автор").first()
                        if not author:
                            author = Author(name="Неизвестный автор", country_id=country.id)
                            db.session.add(author)
                            db.session.commit()

                    # Создаем книгу
                    book = Book(
                        title=title[:255] if title else "Без названия",
                        file_path=filepath,
                        file_format=ext,
                        author_id=author.id,
                        user_id=1,  # Администратор
                        processing_status='pending'
                    )
                    db.session.add(book)
                    db.session.commit()

                    # Запускаем обработку
                    process_uploaded_file.delay(filepath, book.id, ext)
                    logger.info(f"Started processing new file: {filepath}")

                except Exception as e:
                    logger.error(f"Error creating database records: {str(e)}")
                    db.session.rollback()

        except Exception as e:
            logger.error(f"Error processing file {filepath}: {str(e)}", exc_info=True)

    def initial_scan(self):
        """Первоначальное сканирование папки uploads"""
        upload_dir = current_app.config['UPLOAD_FOLDER']
        for root, _, files in os.walk(upload_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                if os.path.isfile(filepath):
                    self.process_file(filepath)


def start_watcher():
    """Запуск наблюдателя за файлами"""
    event_handler = UploadHandler()
    observer = Observer()
    observer.schedule(event_handler, path='uploads', recursive=True)
    observer.start()

    # Первоначальное сканирование
    with current_app.app_context():
        event_handler.initial_scan()

    return observer