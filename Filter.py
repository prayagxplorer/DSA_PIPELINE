import ast
import re
import json
import os
from dotenv import load_dotenv
from datasets import load_dataset

load_dotenv()
token = os.environ["HF_TOKEN"]

taco = load_dataset("BAAI/TACO", token=token, trust_remote_code=True)

NUMBER_PATTERN = re.compile(r"[-+]?\d*\.?\d+")



def has_solution(row):
    raw = row["solutions"]
    if raw is None:
        return False
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return False
    return isinstance(parsed, list) and len(parsed) > 0


def has_valid_limits(row):
    time_ok = bool(NUMBER_PATTERN.search(str(row.get("time_limit"))))
    memory_ok = bool(NUMBER_PATTERN.search(str(row.get("memory_limit"))))
    return time_ok and memory_ok

def get_test_case_count(row):
    raw = row["input_output"]
    if raw is None:
        return 0
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return 0
    inputs = parsed.get("inputs", [])
    return len(inputs) if isinstance(inputs, list) else 0


def has_no_starter_code(row):
    sc = row.get("starter_code")
    return sc is None or sc.strip() == ""


def python_solutions_only(solutions_list):
    python_sols = []
    for sol in solutions_list:
        try:
            ast.parse(sol)
            python_sols.append(sol)
        except SyntaxError:
            continue
    return python_sols


def passes_all_filters(row, min_test_cases=10):
    if not has_solution(row):
        return False
    if not has_no_starter_code(row):
        return False
    if row.get("picture_num") is not None:
        return False
    if get_test_case_count(row) < min_test_cases:
        return False
    if not has_valid_limits(row):          # <- new
        return False
    sols = ast.literal_eval(row["solutions"])
    if not python_solutions_only(sols):
        return False
    return True

def keep_only_python_solutions(row):
    sols = ast.literal_eval(row["solutions"])
    py_sols = python_solutions_only(sols)
    row["solutions"] = json.dumps(py_sols)
    return row


filtered = taco.filter(passes_all_filters)
filtered = filtered.map(keep_only_python_solutions)

for split in filtered.keys():
    print(f"{split}: kept {len(filtered[split])}/{len(taco[split])}")

filtered.save_to_disk("taco_candidates")
