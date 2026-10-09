"""Финальный тест форматирования расписания с исправлениями"""
import sys
from datetime import date

sys.path.insert(0, '.')

from app.services.schedule_service import format_day_schedule
from app.database.models import Lesson

# Создаём тестовую пару
lesson = Lesson(
    lesson_number=4,
    subject='Основы программирования',
    teacher='Сазонова ТВ',
    room='303',
    notes=None,
    group_name='ТД-24-9',
    schedule_date=date(2026, 9, 10),
    source_type='changes',
    course=3
)

# Форматируем
text = format_day_schedule(date(2026, 9, 10), 'ТД-24-9', [lesson])

print("=== ТЕСТ ФОРМАТИРОВАНИЯ РАСПИСАНИЯ ===\n")
print(text)
print("\n=== ПРОВЕРКА ===")
print("✓ Время пары отображается?" , "14:05" in text)
print("✓ Кабинет 303 отображается?", "303" in text)
print("✓ Преподаватель отображается?", "Сазонова" in text)
