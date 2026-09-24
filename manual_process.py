"""Ручная обработка файлов для проверки исправлений"""
import sys
import asyncio
import sqlite3
from pathlib import Path

sys.path.insert(0, '.')

from app.config import get_config
from app.database.database import init_db
from app.services.update_service import check_for_updates

async def main():
    config = get_config()
    init_db(config.database_url)
    
    print("Запуск обработки файлов с исправленным парсером...\n")
    
    try:
        report = await check_for_updates(bot=None)
        print("=== РЕЗУЛЬТАТ ОБРАБОТКИ ===")
        print(report.summary())
    except Exception as e:
        print(f"Ошибка при обработке: {e}")
        import traceback
        traceback.print_exc()
    
    # Проверяем результат в БД
    print("\n=== ПРОВЕРКА БАЗЫ ДАННЫХ ===")
    conn = sqlite3.connect('data/bot.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT lesson_number, subject, teacher, room 
        FROM lessons 
        WHERE group_name=? AND schedule_date=? 
        ORDER BY lesson_number
    ''', ('ТД-24-9', '2026-09-10'))
    
    lessons = cursor.fetchall()
    print(f"\nРасписание ТД-24-9 на 10.09.2026 ({len(lessons)} пар):")
    for lesson in lessons:
        print(f"  Пара {lesson[0]}: {lesson[1][:50]}...")
        print(f"    Преподаватель: {lesson[2]}")
        print(f"    Кабинет: {lesson[3]}")
        print()
    
    conn.close()

if __name__ == '__main__':
    asyncio.run(main())
