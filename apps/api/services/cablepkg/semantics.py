"""Deterministic parsing of cable requirements, independent of seed data."""

import re

from .model import Requirement


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_CONDITION = re.compile(r"[（(]([^()（）]*)[）)]")
_BOUND = re.compile(rf"^(大于等于|不小于|≥|>=|>|小于等于|不大于|≤|<=)\s*(?:±\s*)?({_NUMBER})(.*)$")
_QUALIFIED_NUMBER = re.compile(rf"^({_NUMBER})\s*(%.*)$")
_RANGE = re.compile(rf"^({_NUMBER})\s*[-～~–—]\s*({_NUMBER})$")
_TOLERANCE = re.compile(rf"^(?:({_NUMBER})\s*)?±\s*({_NUMBER})$")


def parse_requirement(raw: str) -> Requirement:
    """Parse operators and values, keeping original text and parenthetic context.

    Standalone ±X becomes [-X, X]; A±X becomes [A-X, A+X].
    For a bound such as ≤±25, value is the magnitude (25). Explicit
    choices separated by 、/或/／/slashes are enums; ordinary prose stays text.
    Names, sequence and units belong to the row metadata and can be supplied
    when constructing Requirement (which also strips name markers).
    """
    result = Requirement(raw=raw)
    text = raw.strip()
    conditions = [match.group(1).strip() for match in _CONDITION.finditer(text)]
    result.unit_condition = "; ".join(conditions) or None
    text = _CONDITION.sub("", text).strip()

    # A placeholder overrides a comparison prefix without inventing a limit.
    if "投标人提供" in raw:
        result.operator = "bidder_supplied"
        result.bidder_supplied = True
        return result
    if text in {"无", "—", "-", "不涉及"}:
        result.operator = "na"
        return result
    if not text:
        return result

    bound = _BOUND.fullmatch(text)
    if bound:
        result.operator = "ge" if bound[1] in {"大于等于", "不小于", "≥", ">=", ">"} else "le"
        result.value = float(bound[2])
        # Retain qualifiers such as %标称厚度; do not mistake them for units.
        result.qualifier = bound[3].strip() or None
        return result

    interval = _RANGE.fullmatch(text)
    tolerance = _TOLERANCE.fullmatch(text)
    qualified_number = _QUALIFIED_NUMBER.fullmatch(text)
    if interval:
        result.operator = "range"
        result.value = [float(interval[1]), float(interval[2])]
    elif tolerance:
        center = float(tolerance[1] or 0)
        delta = abs(float(tolerance[2]))
        result.operator = "range"
        result.value = [center - delta, center + delta]
    elif qualified_number:
        result.operator = "eq"
        result.value = float(qualified_number[1])
        result.qualifier = qualified_number[2].strip()
    elif re.fullmatch(_NUMBER, text):
        result.operator = "eq"
        result.value = float(text)
    else:
        choices = [part.strip() for part in re.split(r"、|或者|或|[/／]|[;；]", text)]
        if len(choices) > 1 and all(choices):
            result.operator = "enum"
            result.value = choices
        else:
            result.operator = "text"
            result.value = raw
    return result
