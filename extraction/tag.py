import ast
from collections import Counter
from datasets import load_from_disk

taco = load_from_disk("taco_candidates")


def count_tag_field(dataset, field):
    counter = Counter()
    empty = 0
    for row in dataset:
        raw = row.get(field)
        if raw is None or raw == "None" or raw.strip() == "":
            empty += 1
            continue
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            empty += 1
            continue
        if isinstance(parsed, list):
            counter.update(parsed)
        elif isinstance(parsed, str):
            counter[parsed] += 1
    return counter, empty


for field in ["raw_tags", "tags", "skill_types"]:
    counter, empty = count_tag_field(taco["train"], field)
    total = len(taco["train"])
    print(f"\n=== {field} ===")
    print(f"empty/unparseable: {empty}/{total} ({empty/total:.1%})")
    print(f"unique values: {len(counter)}")
    print("top 20:")
    for tag, count in counter.most_common(20):
        print(f"  {tag}: {count}")