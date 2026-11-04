"""Optional validity metadata; never used for retrieval or generation decisions."""
import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo
from common.config import get_settings

_DATE = r"(?:\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?|\d{8})"
_LABEL = r"(?:有效(?:期(?:限)?|期限)(?:截止|截至|至)?|营业期限|到期(?:日期|时间)?|截止日期)"

def normalize_date(value: str) -> date | None:
    value = unicodedata.normalize('NFKC', value).strip()
    parts = re.findall(r'\d+', value)
    if len(parts) == 1 and len(parts[0]) == 8:
        raw = parts[0]
        parts = [raw[:4], raw[4:6], raw[6:]]
    try:
        return date(*map(int, parts)) if len(parts) == 3 else None
    except (ValueError, OverflowError):
        return None

def extract_expiry(text: str) -> tuple[date | None, bool]:
    text = unicodedata.normalize('NFKC', text or '')
    if re.search(r"(?m)^\s*(?:长期|永久(?:有效)?|无固定期限)\s*$", text):
        return None, True
    dates = []
    for match in re.finditer(_LABEL + r'\s*[:：]?\s*([^\n;；]{0,100})', text):
        segment = match.group(1)
        if re.search(r'长期|永久|无固定期限', segment):
            return None, True
        found = re.findall(_DATE, segment)
        if found:
            # A range contributes its end, never its issue/start date.
            if len(found) == 1 and re.match(r'\s*(?:自|从)', segment) and not re.search(r'至|到', segment):
                continue
            parsed = normalize_date(found[-1])
            if parsed:
                dates.append(parsed)
    return (min(dates), False) if dates else (None, False)

def apply_expiry(doc, text: str) -> None:
    end, perpetual = extract_expiry(text)
    if end is not None or perpetual:
        doc.expire_at = end
        doc.is_perpetual = perpetual

def expiry_fields(doc, *, today: date | None = None) -> dict:
    settings = get_settings()
    end = getattr(doc, 'expire_at', None)
    perpetual = bool(getattr(doc, 'is_perpetual', False))
    days = (end - (today or datetime.now(ZoneInfo('Asia/Shanghai')).date())).days if end and not perpetual else None
    state, level = 'unknown', 'none'
    if perpetual:
        state = 'perpetual'
    elif days is not None:
        if days < 0:
            state, level = 'expired', 'danger'
        elif days <= max(settings.expire_warn_days, settings.expire_danger_days):
            state = 'expiring'
            level = 'danger' if days <= settings.expire_danger_days else 'warning'
        else:
            state = 'normal'
    return dict(expire_at=end.isoformat() if end else None, is_perpetual=perpetual,
                expire_status=state, expire_level=level, expire_days=days)
