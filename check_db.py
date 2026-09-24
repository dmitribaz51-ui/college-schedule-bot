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
print(f"\nТекущее расписание ТД-24-9 на 10.09.2026 ({len(lessons)} пар):")
for lesson in lessons:
    print(f"  Пара {lesson[0]}: {lesson[1][:50]}...")
    print(f"    Преподаватель: {lesson[2]}")
    print(f"    Кабинет: {lesson[3]}")
    print()

conn.close()
