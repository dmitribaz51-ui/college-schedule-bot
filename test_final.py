"""Финальный тест форматирования расписания с исправлениями"""
import sys
from datetime import date

sys.path.insert(0, '.')

from app.services.schedule_service import format_day_schedule
from app.database.models import Lesson
from app.database.database import init_db, get_session
from app.config import get_config

# Инициализируем БД
config = get_config()
init_db(config.database_url)

# Создаём тестовые пары в БД
session = get_session()

# Очищаем старые данные
session.query(Lesson).filter(
    Lesson.group_name == 'ТД-24-9',
    Lesson.schedule_date == date(2026, 9, 10)
).delete()

# Добавляем тестовые пары
test_lessons = [
    Lesson(
        lesson_number=1,
        subject='Математика',
        teacher='Иванов И.И.',
        room='101',
        group_name='ТД-24-9',
        schedule_date=date(2026, 9, 10),
        source_type='schedule',
        course=3
    ),
    Lesson(
        lesson_number=4,
        subject='Основы программирования',
        teacher='Сазонова Т.В.',
        room='303',
        group_name='ТД-24-9',
        schedule_date=date(2026, 9, 10),
        source_type='changes',
        course=3
    ),
]

for lesson in test_lessons:
    session.add(lesson)
session.commit()

# Тестируем форматирование
text = format_day_schedule('ТД-24-9', date(2026, 9, 10))

print("=== ТЕСТ ФОРМАТИРОВАНИЯ РАСПИСАНИЯ ===\n")
print(text)
print("\n=== ПРОВЕРКА ИСПРАВЛЕНИЙ ===")
print("✓ Время 1-й пары (08:30-10:00):", "08:30" in text and "10:00" in text)
print("✓ Время 4-й пары (14:05-15:35):", "14:05" in text and "15:35" in text)
print("✓ Кабинет 101 отображается:", "101" in text)
print("✓ Кабинет 303 отображается:", "303" in text)
print("✓ Преподаватель отображается:", "Сазонова" in text)

# Очищаем
session.query(Lesson).filter(
    Lesson.group_name == 'ТД-24-9',
    Lesson.schedule_date == date(2026, 9, 10)
).delete()
session.commit()
session.close()
