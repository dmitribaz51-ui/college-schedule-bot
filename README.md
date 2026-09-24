# Telegram-бот расписания ПКПС

## Установка
python -m venv venv
venv\Scripts\activate          # Windows;  Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env         # затем вписать BOT_TOKEN и ADMIN_IDS

## Запуск
python run.py

## Анализ структуры Excel (шаг 14)
python -m tools.analyze_excel data/excel/<файл>.xlsx --rows 40

## Тесты
python -m pytest tests -q      # или просто запустить функции вручную