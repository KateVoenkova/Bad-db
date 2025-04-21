from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import re

db = SQLAlchemy()

def validate_name(name):
    """Валидация имён стран и авторов"""
    if not name or len(name) < 2 or len(name) > 100:
        raise ValueError("Некорректное имя. Длина должна быть от 2 до 100 символов")
    if not re.match(r'^[\w\s\-.,\'а-яА-ЯёЁ]+$', name, re.UNICODE):
        raise ValueError("Некорректное имя. Допустимы только буквы, цифры, пробелы и основные знаки препинания")

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    books = db.relationship('Book', backref='uploader', lazy=True)
    analyses = db.relationship('BookAnalysis', backref='user', lazy=True)

class Country(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    normalized_name = db.Column(db.String(100), unique=True, nullable=False)
    authors = db.relationship('Author', backref='country', lazy=True)

    def __init__(self, **kwargs):
        super(Country, self).__init__(**kwargs)
        validate_name(self.name)
        self.normalized_name = self.name.lower().strip()

class Author(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False, index=True)
    country_id = db.Column(db.Integer, db.ForeignKey('country.id'), nullable=False)
    books = db.relationship('Book', backref='author', lazy=True)

    def __init__(self, **kwargs):
        super(Author, self).__init__(**kwargs)
        validate_name(self.name)
        self.normalized_name = self.name.lower().strip()

class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, default="")
    content = db.Column(db.Text)  # Текст книги
    content_structure = db.Column(db.JSON)  # Структура (главы)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('author.id'), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_format = db.Column(db.String(10), nullable=False)
    processing_status = db.Column(db.String(20), default='pending')
    characters = db.relationship('Character', backref='book', lazy=True)
    analyses = db.relationship('BookAnalysis', backref='book', lazy=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BookAnalysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_deleted = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)

class Character(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False, index=True)
    description = db.Column(db.Text)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)

class CharacterRelationship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    character1_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    character2_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)
    weight = db.Column(db.Integer, default=1)

    __table_args__ = (
        db.UniqueConstraint('character1_id', 'character2_id', 'book_id', name='unique_relationship'),
    )