"""把方案 JSON 藏进 DXF 字典，方便回读；不是图面实体，出图看不见。"""

from __future__ import annotations

import json
from typing import Any

from api.services.layouts.schema import EvChargingStationPlan

_DICT = "WEITAI"
_KEY = "PLAN"
_CHUNK = 200


def embed_plan_json(doc: Any, plan: EvChargingStationPlan) -> None:
    raw = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    chunks = [raw[i : i + _CHUNK] for i in range(0, len(raw), _CHUNK)]
    xdict = doc.rootdict.get_required_dict(_DICT)
    if _KEY in xdict:
        try:
            del xdict[_KEY]
        except Exception:  # noqa: BLE001
            pass
    xrec = doc.objects.add_xrecord()
    xrec.reset([(1, part) for part in chunks])
    xdict[_KEY] = xrec


def extract_plan_json(doc: Any) -> dict[str, Any] | None:
    try:
        xdict = doc.rootdict.get(_DICT)
        if xdict is None:
            return None
        xrec = xdict.get(_KEY)
        if xrec is None:
            return None
        parts = [str(tag.value) for tag in xrec.tags if int(tag.code) == 1]
        if not parts:
            return None
        data = json.loads("".join(parts))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None
