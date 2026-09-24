"""Перезапуск обработки файлов с исправленным парсером"""
import sys
from pathlib import Path

sys.path.insert(0, '.')

from app.config import get_config
from app.database.database import init_db, get_session
from app.parser.processor import process_all_files

def main():
    config = get_config()
    init_db(config.database_url)
    
    print("Начинаю обработку файлов с исправленным парсером...")
    with get_session() as db:
        process_all_files(db)
    
    print("\nГотово! Проверяем результат для ТД-24-9...")
    
    import sqlite3
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
    main()
