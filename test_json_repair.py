"""Standalone test for extraction optimization components."""
import json
import re


def repair_json(raw: str) -> str:
    """Attempt to repair common JSON issues before parsing."""
    text = raw.strip()

    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find JSON object boundaries
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]

    # Remove trailing commas
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    return text


def test_json_repair():
    """Test JSON repair logic."""
    test_cases = [
        ('```json\n{"key": "value"}\n```', "markdown wrapper"),
        ('Some text before\n{"key": "value"}\nSome text after', "extra text"),
        ('{"key": "value",}', "trailing comma in object"),
        ('{"list": [1, 2, 3,]}', "trailing comma in array"),
        ('{"nested": {"key": "value",},}', "nested trailing commas"),
        ('```\n{"projectName": "测试项目", "quoteLines": [],}\n```', "realistic case"),
    ]

    print("JSON repair test:")
    passed = 0
    for raw, description in test_cases:
        try:
            repaired = repair_json(raw)
            parsed = json.loads(repaired)
            print(f"[PASS] {description}: success")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {description}: {e}")

    print(f"\nPassed {passed}/{len(test_cases)} tests")
    return passed == len(test_cases)


if __name__ == "__main__":
    success = test_json_repair()
    exit(0 if success else 1)
