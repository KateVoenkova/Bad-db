# test_names.py
from name_parser import extract_all_names

sample_text = """
    В романе встречаются такие персонажи как Анна Каренина, Алексей Вронский, граф Левин.
    Также присутствует князь Болконский и мистер Дарси.
    А вот сэр Джон Сноу выглядел иначе.
"""

names = extract_all_names(sample_text)
print("Найденные имена:")
for name in names:
    print(f"- {name}")
