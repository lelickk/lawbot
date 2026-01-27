from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session

# 1. Описание таблицы Клиентов
class Client(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phone_number: str = Field(index=True, unique=True) # Номер телефона (уникальный)
    full_name: Optional[str] = None                    # ФИО (узнаем позже)
    folder_id: Optional[str] = None                    # ID папки на Google Drive

# 2. Описание таблицы Документов
class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")    # Чей это документ
    doc_type: str                                      # Например: "Паспорт"
    status: str = "received"                           # Статус: received, processed, approved
    file_path: str                                     # Где лежит файл
    created_at: str                                    # Дата загрузки

# 3. Настройка подключения к файлу базы данных
sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

engine = create_engine(sqlite_url)

# Функция для создания таблиц (запустим её один раз)
def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

# Функция для получения сессии (чтобы работать с БД)
def get_session():
    with Session(engine) as session:
        yield session