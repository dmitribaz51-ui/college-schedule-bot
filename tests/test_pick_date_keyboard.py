"""Тесты для inline-кнопок быстрого выбора даты."""
from datetime import date

from app.bot.keyboards.menu import pick_date_keyboard


def test_weekday_anchors():
    """Якоря дней недели: 27.09→вс, 28.09→пн, 30.09→ср, 30.10→пт."""
    anchors = [
        (date(2026, 9, 27), "вс"),
        (date(2026, 9, 28), "пн"),
        (date(2026, 9, 30), "ср"),
        (date(2026, 10, 30), "пт"),
    ]
    for d, expected_wd in anchors:
        kb = pick_date_keyboard([d])
        assert kb is not None
        btn = kb.inline_keyboard[0][0]
        assert expected_wd in btn.text, f"{d.isoformat()} должна давать '{expected_wd}', получили: {btn.text}"
    print("✓ test_weekday_anchors")


def test_seven_dates_gives_three_buttons():
    """7 дат → ровно 3 кнопки, первая = самой свежей."""
    dates = [date(2026, 9, 30), date(2026, 9, 29), date(2026, 9, 28),
             date(2026, 9, 27), date(2026, 9, 26), date(2026, 9, 25), date(2026, 9, 24)]
    kb = pick_date_keyboard(dates)
    assert kb is not None
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 3
    first = kb.inline_keyboard[0][0]
    assert "30.09" in first.text
    print("✓ test_seven_dates_gives_three_buttons")


def test_one_or_two_dates():
    """1–2 даты → соответствующее число кнопок."""
    kb1 = pick_date_keyboard([date(2026, 9, 30)])
    assert kb1 is not None
    assert len(kb1.inline_keyboard[0]) == 1

    kb2 = pick_date_keyboard([date(2026, 9, 30), date(2026, 9, 29)])
    assert kb2 is not None
    assert len(kb2.inline_keyboard[0]) == 2
    print("✓ test_one_or_two_dates")


def test_empty_list_returns_none():
    """Пустой список → None."""
    assert pick_date_keyboard([]) is None
    print("✓ test_empty_list_returns_none")


def test_callback_data_format():
    """callback_data = pickdate: + ISO-дата, обратно парсится."""
    d = date(2026, 9, 30)
    kb = pick_date_keyboard([d])
    assert kb is not None
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data.startswith("pickdate:")
    raw = btn.callback_data.removeprefix("pickdate:")
    parsed = date.fromisoformat(raw)
    assert parsed == d
    print("✓ test_callback_data_format")


if __name__ == "__main__":
    test_weekday_anchors()
    test_seven_dates_gives_three_buttons()
    test_one_or_two_dates()
    test_empty_list_returns_none()
    test_callback_data_format()
    print("\n✅ Все тесты прошли")
