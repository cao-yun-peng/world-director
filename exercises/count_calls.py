"""基础训练：用字典统计工具调用次数。"""


def count_calls(calls: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for call in calls:
        counts[call] = counts.get(call, 0) + 1
    return counts


if __name__ == "__main__":
    calls = ["inspect_object", "talk", "inspect_object", "move", "inspect_object", "talk"]
    print(count_calls(calls))
    print(count_calls([]))
