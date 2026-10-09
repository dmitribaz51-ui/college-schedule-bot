"""Process pending files manually"""
import sys
sys.path.insert(0, '.')

from pathlib import Path
from app.config import get_config
from app.database.database import init_db, get_session
from app.database import repository as repo
from app.parser.excel_parser import parse_excel_file

config = get_config()
init_db(config.database_url)

with get_session() as db:
    pending = db.execute("SELECT id, local_path, schedule_date, file_type FROM schedule_files WHERE processed = 0 AND local_path IS NOT NULL").fetchall()
    
    print(f"Found {len(pending)} pending files")
    
    for file_id, path, sched_date, file_type in pending:
        print(f"\nProcessing: {path}")
        
        try:
            schedules = parse_excel_file(Path(path), default_date=sched_date)
            
            saved_groups = []
            saved_lessons = 0
            
            for schedule in schedules:
                day = schedule.schedule_date or sched_date
                if not day or not schedule.lessons:
                    continue
                    
                saved_lessons += repo.replace_group_lessons(
                    schedule_date=day,
                    group_name=schedule.group,
                    source_type=file_type,
                    course=schedule.course,
                    lessons=[l.to_dict() for l in schedule.lessons],
                    source_file_id=file_id,
                )
                saved_groups.append(schedule.group)
            
            db.execute(
                "UPDATE schedule_files SET processed = 1, error = NULL WHERE id = ?",
                (file_id,)
            )
            db.commit()
            
            print(f"  Saved {saved_lessons} lessons for {len(saved_groups)} groups")
            
        except Exception as e:
            print(f"  ERROR: {e}")
            db.execute(
                "UPDATE schedule_files SET processed = 0, error = ? WHERE id = ?",
                (str(e), file_id)
            )
            db.commit()

print("\nDone. Checking results...")

import sqlite3
conn = sqlite3.connect('data/bot.db')
cursor = conn.cursor()
cursor.execute("SELECT COUNT(*) FROM lessons WHERE schedule_date = '2026-09-10' AND group_name = 'ТД-24-9'")
count = cursor.fetchone()[0]
print(f"ТД-24-9 on 2026-09-10: {count} lessons")
conn.close()
