from typing import Optional, List
from datetime import datetime
from sqlmodel import Field, SQLModel, create_engine, Session, select

# 1. Настройка базы данных (файл будет лежать прямо в папке)
DATABASE_URL = "sqlite:///./lawbot.db"
engine = create_engine(DATABASE_URL)

# 2. Таблица Клиентов
class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phone_number: str = Field(index=True, unique=True)
    full_name: str
    created_at: datetime = Field(default_factory=datetime.now)

# 3. Таблица Документов
class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    doc_type: str
    file_path: str  # Путь на Яндекс.Диске
    created_at: datetime = Field(default_factory=datetime.now)

# Функция создания таблиц (запускается при старте)
def init_db():
    SQLModel.metadata.create_all(engine)

# Функция для получения сессии (подключения)
def get_session():
    with Session(engine) as session:
        yield session