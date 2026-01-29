import os
from typing import Optional
from datetime import datetime
from sqlmodel import Field, SQLModel, create_engine, Session

# Читаем путь из переменной окружения (которую мы задали в docker-compose)
# Если переменной нет (локальный тест), кладем рядом
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lawbot.db")

engine = create_engine(DATABASE_URL)

class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phone_number: str = Field(index=True, unique=True)
    full_name: str
    created_at: datetime = Field(default_factory=datetime.now)

class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    doc_type: str
    file_path: str
    created_at: datetime = Field(default_factory=datetime.now)

def init_db():
    SQLModel.metadata.create_all(engine)