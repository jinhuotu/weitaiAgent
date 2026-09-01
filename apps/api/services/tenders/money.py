"""人民币金额转中文大写。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_SMALL = ("", "拾", "佰", "仟")
_BIG = ("", "万", "亿")


def _int_part(n: int) -> str:
    if n == 0:
        return _DIGITS[0]
    parts: list[str] = []
    group = 0
    while n > 0:
        chunk = n % 10000
        n //= 10000
        if chunk:
            parts.append(_four(chunk) + _BIG[group])
        elif parts:
            parts.append(_DIGITS[0])
        group += 1
    parts.reverse()
    text = "".join(parts)
    while "零零" in text:
        text = text.replace("零零", "零")
    return text.rstrip("零") or _DIGITS[0]


def _four(n: int) -> str:
    if n == 0:
        return ""
    chars: list[str] = []
    last_zero = False
    for i in range(3, -1, -1):
        unit = 10**i
        d = (n // unit) % 10
        if d:
            if last_zero:
                chars.append(_DIGITS[0])
            chars.append(_DIGITS[d] + _SMALL[i])
            last_zero = False
        else:
            last_zero = bool(chars)
    return "".join(chars)


def rmb_uppercase(amount: float | int | str | Decimal) -> str:
    """518800 -> 伍拾壹万捌仟捌佰元整。"""
    q = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q < 0:
        raise ValueError("amount must be >= 0")
    yuan = int(q)
    fen = int((q - Decimal(yuan)) * 100)
    jiao, fen = divmod(fen, 10)
    text = _int_part(yuan) + "元"
    if jiao == 0 and fen == 0:
        return text + "整"
    if jiao:
        text += _DIGITS[jiao] + "角"
    elif fen:
        text += "零"
    if fen:
        text += _DIGITS[fen] + "分"
    return text


def rmb_lowercase(amount: float | int | str | Decimal) -> str:
    q = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{q:,.2f}"
