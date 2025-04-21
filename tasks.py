from celery import Celery
from flask import current_app
from models import db, Book, Character, CharacterRelationship
import os
import PyPDF2
from docx import Document
from bs4 import BeautifulSoup
import zipfile
import chardet
import tempfile
import re
from name_parser import extract_all_names
from relationships import RelationshipFinder
from ebooklib import epub

celery = Celery(__name__, broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')


def init_celery(app):
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


def read_text_file(filepath: str) -> str:
    """Чтение текстового файла с обработкой разных кодировок"""
    encodings = ['utf-8', 'windows-1251', 'cp1252', 'iso-8859-1', 'koi8-r']
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode file {filepath} with any supported encoding")


def read_pdf_file(filepath: str) -> str:
    """Извлечение текста из PDF"""
    text = ""
    try:
        with open(filepath, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() + "\n"
    except Exception as e:
        current_app.logger.error(f"Error reading PDF {filepath}: {str(e)}")
    return text


def read_docx_file(filepath: str) -> str:
    """Извлечение текста из DOCX"""
    try:
        doc = Document(filepath)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        current_app.logger.error(f"Error reading DOCX {filepath}: {str(e)}")
        return ""


def read_epub_with_structure(filepath):
    """Чтение EPUB с сохранением структуры"""
    try:
        book = epub.read_epub(filepath, options={'ignore_ncx': True})
        text = ""
        structure = []

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            try:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                chapter_title = soup.find(['h1', 'h2', 'h3']) or f"Глава {len(structure) + 1}"
                chapter_text = ' '.join(soup.stripped_strings)

                structure.append({
                    'title': str(chapter_title),
                    'text': chapter_text
                })
                text += chapter_text + "\n\n"
            except Exception as e:
                current_app.logger.warning(f"Skipping EPUB section: {str(e)}")
                continue

        return text, structure
    except Exception as e:
        current_app.logger.error(f"Error reading EPUB file {filepath}: {str(e)}")
        return "", []


def parse_fb2_soup(soup):
    """Парсинг BeautifulSoup объекта FB2"""
    text = ""
    structure = []

    for section in soup.find_all('section'):
        title = section.find('title')
        chapter_title = title.text if title else "Глава"
        chapter_text = ' '.join(section.stripped_strings)

        structure.append({
            'title': chapter_title,
            'text': chapter_text
        })
        text += chapter_text + "\n\n"

    return text, structure


def read_fb2_with_structure(filepath):
    """Чтение FB2 с сохранением структуры"""
    encodings = ['utf-8', 'windows-1251', 'cp1251', 'koi8-r']

    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                content = f.read()
                soup = BeautifulSoup(content, 'xml')
                return parse_fb2_soup(soup)
        except UnicodeDecodeError:
            continue
        except Exception as e:
            current_app.logger.error(f"Error reading FB2 file {filepath}: {str(e)}")
            continue

    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            for name in z.namelist():
                if name.lower().endswith('.fb2'):
                    with z.open(name) as f:
                        content = f.read()
                        for encoding in encodings:
                            try:
                                decoded = content.decode(encoding)
                                soup = BeautifulSoup(decoded, 'xml')
                                return parse_fb2_soup(soup)
                            except:
                                continue
    except Exception as e:
        current_app.logger.error(f"Error reading FB2 as ZIP {filepath}: {str(e)}")

    return "", []


def handle_zip_file(filepath):
    """Обработка ZIP-архива"""
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            for name in z.namelist():
                if name.lower().endswith('.fb2'):
                    with z.open(name) as f:
                        content = f.read().decode('utf-8')
                        soup = BeautifulSoup(content, 'xml')
                        text, _ = parse_fb2_soup(soup)
                        return text
    except Exception as e:
        current_app.logger.error(f"Error processing ZIP file {filepath}: {str(e)}")
    return ""


@celery.task(bind=True)
def process_uploaded_file(self, filepath: str, book_id: int, file_format: str):
    """Основная задача обработки файла"""
    with current_app.app_context():
        try:
            book = Book.query.get(book_id)
            if not book:
                current_app.logger.error(f"[Book {book_id}] Книга не найдена")
                return False

            book.processing_status = 'processing'
            db.session.commit()

            # ---------- Чтение файла ----------
            text = ""
            structure = []

            try:
                if file_format == 'txt':
                    text = read_text_file(filepath)
                elif file_format == 'pdf':
                    text = read_pdf_file(filepath)
                elif file_format == 'docx':
                    text = read_docx_file(filepath)
                elif file_format == 'epub':
                    text, structure = read_epub_with_structure(filepath)
                elif file_format == 'fb2':
                    text, structure = read_fb2_with_structure(filepath)
                elif file_format == 'zip':
                    text = handle_zip_file(filepath)
                else:
                    book.processing_status = 'failed: unsupported format'
                    db.session.commit()
                    return False
            except Exception as e:
                current_app.logger.error(f"[Book {book_id}] Ошибка чтения файла: {str(e)}")
                book.processing_status = 'failed: error reading file'
                db.session.commit()
                return False

            if not text.strip():
                current_app.logger.error(f"[Book {book_id}] Пустой текст после чтения")
                book.processing_status = 'failed: empty content'
                db.session.commit()
                return False

            # ---------- Сохраняем текст и структуру ----------
            book.content = text
            book.content_structure = structure if structure else None
            db.session.commit()

            # ---------- Извлечение персонажей ----------
            names = extract_all_names(text)
            current_app.logger.info(f"[Book {book_id}] Найдено персонажей: {names}")

            # Удалим старых
            Character.query.filter_by(book_id=book_id).delete()

            if names:
                for name in names:
                    character = Character(
                        name=name,
                        normalized_name=name.lower(),
                        book_id=book_id
                    )
                    db.session.add(character)
                db.session.commit()
            else:
                current_app.logger.warning(f"[Book {book_id}] Не найдено ни одного персонажа")

            # ---------- Анализ отношений ----------
            finder = RelationshipFinder(book_id)
            finder.process_text(text)

            # ---------- Завершение ----------
            book.processing_status = 'completed'
            db.session.commit()

            current_app.logger.info(f"[Book {book_id}] Обработка завершена успешно")
            return True

        except Exception as e:
            current_app.logger.error(f"[Book {book_id}] Ошибка при обработке: {str(e)}", exc_info=True)
            book.processing_status = f'failed: {str(e)}'
            db.session.commit()
            return False



@celery.task(bind=True)
def scan_uploads_folder(self):
    """Сканирование папки uploads при запуске"""
    with current_app.app_context():
        upload_dir = current_app.config['UPLOAD_FOLDER']
        for root, _, files in os.walk(upload_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                if os.path.isfile(filepath):
                    parts = filepath.split(os.sep)
                    if len(parts) >= 4:
                        country_name = parts[-3]
                        author_name = parts[-2]
                        title, ext = os.path.splitext(filename)
                        ext = ext[1:].lower()

                        if ext not in current_app.config['ALLOWED_EXTENSIONS']:
                            continue

                        existing = Book.query.filter_by(file_path=filepath).first()
                        if not existing:
                            country = Country.query.filter_by(normalized_name=country_name.lower()).first()
                            if not country:
                                country = Country(name=country_name)
                                db.session.add(country)
                                db.session.commit()

                            author = Author.query.filter_by(normalized_name=author_name.lower()).first()
                            if not author:
                                author = Author(name=author_name, country_id=country.id)
                                db.session.add(author)
                                db.session.commit()

                            book = Book(
                                title=title,
                                file_path=filepath,
                                file_format=ext,
                                author_id=author.id,
                                user_id=1,
                                processing_status='pending'
                            )
                            db.session.add(book)
                            db.session.commit()

                            process_uploaded_file.delay(filepath, book.id, ext)