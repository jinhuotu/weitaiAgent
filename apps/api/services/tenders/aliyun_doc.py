"""文档智能：本地 PDF 转 Word。只负责转换，不改版式。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger("api.tenders.docmind")


def convert_pdf_to_docx(pdf: Path, dest: Path) -> None:
    from common.config import get_settings

    settings = get_settings()
    ak = (settings.docmind_access_key_id or settings.ocr_access_key_id or "").strip()
    sk = (settings.docmind_access_key_secret or settings.ocr_access_key_secret or "").strip()
    if not ak or not sk:
        raise RuntimeError("未配置 DOCMIND_ACCESS_KEY_ID / DOCMIND_ACCESS_KEY_SECRET")
    endpoint = (settings.docmind_endpoint or "docmind-api.cn-hangzhou.aliyuncs.com").strip()
    option = (settings.docmind_pdf_option or "basic").strip() or "basic"
    timeout = int(settings.docmind_timeout_seconds or 360)
    job_id = _submit(pdf, ak, sk, endpoint, option)
    url = _wait(job_id, ak, sk, endpoint, timeout)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.content
    if len(data) < 800 or not data.startswith(b"PK"):
        raise RuntimeError("阿里云返回的不是有效 Word 文件")
    dest.write_bytes(data)
    logger.info("docmind pdf->docx ok bytes=%s", len(data))


def _client(ak: str, sk: str, endpoint: str):
    from alibabacloud_docmind_api20220711.client import Client
    from alibabacloud_tea_openapi import models as open_api_models

    config = open_api_models.Config(
        access_key_id=ak,
        access_key_secret=sk,
        endpoint=endpoint,
    )
    config.connect_timeout = 20000
    config.read_timeout = 60000
    return Client(config)


def _submit(pdf: Path, ak: str, sk: str, endpoint: str, option: str) -> str:
    from alibabacloud_docmind_api20220711 import models as dm
    from alibabacloud_tea_util import models as util_models

    client = _client(ak, sk, endpoint)
    name = pdf.name if pdf.suffix.lower() == ".pdf" else "format.pdf"
    runtime = util_models.RuntimeOptions()
    with pdf.open("rb") as fh:
        req = dm.SubmitConvertPdfToWordJobAdvanceRequest(
            file_url_object=fh,
            file_name=name,
            option=option,
        )
        try:
            resp = client.submit_convert_pdf_to_word_job_advance(req, runtime)
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc)
            raise RuntimeError(f"提交 PDF 转 Word 失败：{msg}") from exc
    data = getattr(getattr(resp, "body", None), "data", None)
    job_id = getattr(data, "id", None) if data is not None else None
    if not job_id:
        raise RuntimeError("阿里云未返回转换任务号")
    return str(job_id)


def _wait(job_id: str, ak: str, sk: str, endpoint: str, timeout: int) -> str:
    from alibabacloud_docmind_api20220711 import models as dm

    client = _client(ak, sk, endpoint)
    req = dm.GetDocumentConvertResultRequest(id=job_id)
    deadline = time.time() + max(30, timeout)
    while time.time() < deadline:
        try:
            resp = client.get_document_convert_result(req)
        except Exception as exc:
            msg = getattr(exc, "message", None) or str(exc)
            raise RuntimeError(f"查询 PDF 转 Word 失败：{msg}") from exc
        body = getattr(resp, "body", None)
        if not bool(getattr(body, "completed", False)):
            time.sleep(5)
            continue
        status = (getattr(body, "status", None) or "").strip().lower()
        if status != "success":
            msg = getattr(body, "message", None) or getattr(body, "code", None) or status or "转换失败"
            raise RuntimeError(f"PDF 转 Word 失败：{msg}")
        data = getattr(body, "data", None) or []
        item = data[0] if isinstance(data, (list, tuple)) and data else data
        url = getattr(item, "url", None) if item is not None else None
        if not url:
            raise RuntimeError("PDF 转 Word 未返回下载地址")
        return str(url)
    raise RuntimeError("PDF 转 Word 超时")
