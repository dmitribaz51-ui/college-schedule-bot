"""Обработка необработанных файлов при старте бота."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_config
from app.database.database import init_db
from app.database import repository as repo
from app.parser.excel_parser import parse_excel_file

logger = logging.getLogger(__name__)


def process_pending_files() -> int:
    """Обрабатывает все файлы с processed=0, у которых есть local_path.
    
    Возвращает количество успешно обработанных файлов.
    """
    import sqlite3
    
    config = get_config()
    init_db(config.database_url)
    
    processed_count = 0
    
    conn = sqlite3.connect('data/bot.db')
    cursor = conn.cursor()
    
    # Находим все необработанные файлы
    cursor.execute(
        "SELECT id, local_path, schedule_date, file_type, title "
        "FROM schedule_files WHERE processed = 0 AND local_path IS NOT NULL"
    )
    pending = cursor.fetchall()
    
    if not pending:
        logger.info("Нет необработанных файлов")
        conn.close()
        return 0
    
    logger.info(f"Найдено {len(pending)} необработанных файлов")
    
    for file_id, local_path, schedule_date, file_type, title in pending:
        file_path = Path(local_path)
        
        if not file_path.exists():
            logger.warning(f"Файл не найден: {local_path}")
            cursor.execute(
                "UPDATE schedule_files SET error = ? WHERE id = ?",
                ("Файл не найден на диске", file_id)
            )
            conn.commit()
            continue
        
        try:
            logger.info(f"Обработка: {title}")
            schedules = parse_excel_file(file_path, default_date=schedule_date)
            
            saved_groups = []
            saved_lessons = 0
            
            for schedule in schedules:
                day = schedule.schedule_date or schedule_date
                if not day or not schedule.lessons:
                    continue
                
                count = repo.replace_group_lessons(
                    schedule_date=day,
                    group_name=schedule.group,
                    source_type=file_type,
                    course=schedule.course,
                    lessons=[l.to_dict() for l in schedule.lessons],
                    source_file_id=file_id,
                )
                saved_lessons += count
                saved_groups.append(schedule.group)
            
            if saved_groups:
                cursor.execute(
                    "UPDATE schedule_files SET processed = 1, error = NULL WHERE id = ?",
                    (file_id,)
                )
                conn.commit()
                logger.info(f"✓ Сохранено {saved_lessons} пар для {len(saved_groups)} групп")
                processed_count += 1
            else:
                cursor.execute(
                    "UPDATE schedule_files SET error = ? WHERE id = ?",
                    ("Не удалось распознать ни одной группы", file_id)
                )
                conn.commit()
                logger.warning(f"Группы не распознаны в {title}")
                
        except Exception as e:
            logger.exception(f"Ошибка обработки {title}")
            cursor.execute(
                "UPDATE schedule_files SET error = ? WHERE id = ?",
                (str(e), file_id)
            )
            conn.commit()
    
    conn.close()
    logger.info(f"Обработано файлов: {processed_count}/{len(pending)}")
    return processed_count


if __name__ == "__main__":
    from app.utils import setup_logging
    setup_logging("INFO")
    process_pending_files()
