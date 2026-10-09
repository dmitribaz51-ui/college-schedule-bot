"""Тест исправленного парсера"""
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.parser.excel_parser import split_lesson_text

# Тестовые случаи
test_cases = [
    "Основы программирования на Java Сазонова ТВ / 303 дистант",
    "Физическая культура Двинянинова ИИ / 2",
    "Введение в специальность Иванова ОМ / 101",
    "Физика Стук АК / 204А",
]

print("=== Тест парсера кабинетов ===\n")
for text in test_cases:
    result = split_lesson_text(text)
    print(f"Исходный текст: {text}")
    print(f"  Предмет: {result.subject}")
    print(f"  Преподаватель: {result.teacher}")
    print(f"  Кабинет: {result.room}")
    print(f"  Примечание: {result.notes}")
    print()
