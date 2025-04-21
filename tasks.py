from celery import Celery
from flask import current_app
from models import db, Book, Character, CharacterRelationship
import os
import PyPDF2
from docx import Document
import ebooklib
from ebooklib import epub
import chardet
import tempfile
from name_parser import get_names_from_file
from relationships import RelationshipFinder

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
    """Чтение текстового файла с определением кодировки"""
    with open(filepath, 'rb') as f:
        raw_data = f.read()
        encoding = chardet.detect(raw_data)['encoding']

    with open(filepath, 'r', encoding=encoding) as f:
        return f.read()


def read_pdf_file(filepath: str) -> str:
    """Извлечение текста из PDF"""
    text = ""
    with open(filepath, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() + "\n"
    return text


def read_docx_file(filepath: str) -> str:
    """Извлечение текста из DOCX"""
    doc = Document(filepath)
    return "\n".join([para.text for para in doc.paragraphs])


def read_epub_file(filepath: str) -> str:
    """Извлечение текста из EPUB"""
    book = epub.read_epub(filepath)
    text = ""
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            text += item.get_content().decode('utf-8', 'ignore') + "\n"
    return text


def read_fb2_file(filepath: str) -> str:
    """Извлечение текста из FB2"""
    import zipfile
    from bs4 import BeautifulSoup

    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            with z.open(z.namelist()[0]) as f:
                soup = BeautifulSoup(f.read(), 'xml')
                return ' '.join(soup.stripped_strings)
    except:
        # Если это не zip, попробуем как обычный XML
        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'xml')
            return ' '.join(soup.stripped_strings)

def handle_zip_file(filepath):
    """Обработка ZIP-архива (предполагаем, что внутри FB2)"""
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            for name in z.namelist():
                if name.lower().endswith('.fb2'):
                    with z.open(name) as f:
                        soup = BeautifulSoup(f.read(), 'xml')
                        return ' '.join(soup.stripped_strings)
        return ""
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
                current_app.logger.error(f"Book {book_id} not found")
                return False

            book.processing_status = 'processing'
            db.session.commit()

            # Чтение файла в зависимости от формата
            if file_format == 'txt':
                text = read_text_file(filepath)
            elif file_format == 'pdf':
                text = read_pdf_file(filepath)
            elif file_format == 'docx':
                text = read_docx_file(filepath)
            elif file_format == 'epub':
                text = read_epub_file(filepath)
            elif file_format == 'fb2':
                text = read_fb2_file(filepath)
            elif file_format == 'zip':
                text = handle_zip_file(filepath)
            else:
                book.processing_status = 'failed: unsupported format'
                db.session.commit()
                return False

            if not text:
                book.processing_status = 'failed: empty content'
                db.session.commit()
                return False

            # Извлечение и сохранение персонажей
            try:
                names = extract_all_names(text)
            except Exception as e:
                current_app.logger.error(f"Error extracting names: {str(e)}")
                names = []

            # Удаляем старых персонажей
            Character.query.filter_by(book_id=book_id).delete()

            # Добавляем новых
            for name in names:
                try:
                    char = Character(
                        name=name,
                        normalized_name=name.lower(),
                        book_id=book_id
                    )
                    db.session.add(char)
                except Exception as e:
                    current_app.logger.error(f"Error adding character {name}: {str(e)}")

            db.session.commit()

            # Анализ отношений
            try:
                finder = RelationshipFinder(book_id)
                finder.process_text(text)
            except Exception as e:
                current_app.logger.error(f"Error analyzing relationships: {str(e)}")

            book.processing_status = 'completed'
            db.session.commit()
            return True

        except Exception as e:
            current_app.logger.error(f"Error processing book {book_id}: {str(e)}", exc_info=True)
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
                    if len(parts) >= 4:  # uploads/country/author/file
                        country_name = parts[-3]
                        author_name = parts[-2]
                        title, ext = os.path.splitext(filename)
                        ext = ext[1:].lower()

                        if ext not in current_app.config['ALLOWED_EXTENSIONS']:
                            continue

                        # Проверяем существование книги
                        existing = Book.query.filter_by(file_path=filepath).first()
                        if not existing:
                            # Создаем страну и автора
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

                            # Создаем книгу
                            book = Book(
                                title=title,
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


def extract_metadata_from_file(filepath, file_format):
    """Извлечение метаданных из содержимого файла"""
    try:
        if file_format == 'epub':
            import epub
            book = epub.read_epub(filepath)
            title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else None
            creator = book.get_metadata('DC', 'creator')[0][0] if book.get_metadata('DC', 'creator') else None
            return creator or "Неизвестный автор", title or "Без названия"

        elif file_format == 'fb2':
            from bs4 import BeautifulSoup
            with open(filepath, 'r', encoding='utf-8') as f:
                soup = BeautifulSoup(f.read(), 'xml')
                title = soup.find('book-title').text if soup.find('book-title') else None
                author = soup.find('first-name').text + ' ' + soup.find('last-name').text if soup.find(
                    'first-name') and soup.find('last-name') else None
                return author or "Неизвестный автор", title or "Без названия"

        # Аналогично для других форматов...

    except Exception as e:
        current_app.logger.error(f"Error extracting metadata: {str(e)}")
        return None, None


def convert_to_html(filepath, file_format):
    """Конвертация файла в HTML для онлайн-чтения"""
    if file_format == 'epub':
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup

        book = epub.read_epub(filepath)
        html = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                html.append(str(soup))
        return '\n'.join(html)

    elif file_format == 'fb2':
        from bs4 import BeautifulSoup
        with open(filepath, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f.read(), 'xml')
            # Преобразуем FB2 в HTML
            return str(soup)

    return ""