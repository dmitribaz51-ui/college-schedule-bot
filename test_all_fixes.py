"""Verify all fixes - parser, time, formatting"""
import sys
sys.path.insert(0, '.')

from app.parser.excel_parser import split_lesson_text
from app.utils import LESSON_TIMES

print("=== TEST 1: Room Parser ===")
tests = [
    ('Teacher / 303', '303'),
    ('Name / 2', '2'),
    ('Test / 101A', '101A'),
]

parser_ok = True
for text, expected in tests:
    result = split_lesson_text(text).room
    ok = result == expected
    parser_ok = parser_ok and ok
    print(f'{text:20} -> {result:5} [{"OK" if ok else "FAIL"}]')

print(f'\nParser: {"PASS" if parser_ok else "FAIL"}\n')

print("=== TEST 2: Lesson Times ===")
time_tests = [(1, '08:30', '10:00'), (4, '14:05', '15:35')]

time_ok = True
for num, start, end in time_tests:
    actual = LESSON_TIMES[num]
    ok = actual == (start, end)
    time_ok = time_ok and ok
    print(f'Lesson {num}: {actual[0]}-{actual[1]} [{"OK" if ok else "FAIL"}]')

print(f'\nTimes: {"PASS" if time_ok else "FAIL"}\n')

print("=== FINAL RESULT ===")
all_ok = parser_ok and time_ok
print(f'{"ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED"}')
