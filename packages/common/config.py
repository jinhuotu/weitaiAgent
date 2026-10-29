"""环境变量配置（pydantic-settings）。

读取优先级：进程环境变量 > 仓库根目录 .env > 本文件默认值。
"""

import socket
from functools import lru_cache
from ipaddress import AddressValueError, IPv4Address

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _lan_ipv4() -> str | None:
    """Prefer the current outbound LAN interface, then local IPv4 interfaces."""
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # UDP connect chooses an interface without sending data to this address.
            sock.connect(("8.8.8.8", 80))
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(
            address[4][0]
            for address in socket.getaddrinfo(
                socket.gethostname(), None, family=socket.AF_INET, type=socket.SOCK_DGRAM
            )
        )
    except OSError:
        pass
    for candidate in candidates:
        try:
            ip = IPv4Address(candidate)
        except (AddressValueError, ValueError):
            continue
        if ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
            return str(ip)
    return None


def _default_onlyoffice_document_server_url() -> str:
    return f"http://{_lan_ipv4() or '127.0.0.1'}:8082"


class Settings(BaseSettings):
    """应用运行时配置。字段名对应环境变量（大写 + 下划线）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "weitai-agent"
    app_env: str = "dev"
    debug: bool = True
    api_prefix: str = "/api/v1"
    # "*" = 允许任意 Origin（开发默认）。生产请设前端域名白名单。
    cors_origins: str = "*"

    api_host: str = "0.0.0.0"
    # 避开 zhongjiAgent 默认 :8000
    api_port: int = 8100

    database_url: str = (
        "mysql+asyncmy://weitai:weitai_dev@127.0.0.1:13306/weitai_agent"
    )
    # Docker Compose 默认映射宿主机 26379 → 容器 6379（避开 zhongji 的 16379）
    redis_url: str = "redis://127.0.0.1:26379/0"

    # ---- 对话热记忆 / Stream 归档 ----
    # Redis List TTL：活跃会话在 Redis，过期前由 Worker 补写 MySQL
    chat_session_ttl_seconds: int = 7 * 24 * 3600
    # 冷启动：Redis miss 时从 MySQL 回填最近 N 轮（user+assistant 算一轮）
    chat_cold_start_turns: int = 15
    chat_stream_key: str = "agent:chat:stream"
    chat_stream_dlq_key: str = "agent:chat:stream:dql"
    chat_stream_group: str = "chat-archiver"
    chat_stream_consumer_prefix: str = "worker"
    chat_stream_batch_size: int = 30
    chat_stream_block_ms: int = 5000

    # ---- 裁剪 / 限流 / 会话锁 ----
    chat_trim_trigger_turns: int = 25
    chat_trim_keep_turns: int = 10
    chat_rate_limit_max: int = 30
    chat_rate_limit_window_seconds: int = 60
    # 多轮生成可能超过 2 分钟；配合锁续租避免提前过期
    chat_session_lock_ttl_seconds: int = 300
    chat_ttl_scan_threshold_seconds: int = 3600
    chat_ttl_scan_cron: str = "0 3 * * *"  # 每天 03:00
    # 应用日志目录（按天文件 weitai-agent.YYYY-MM-DD.log）
    log_dir: str = "./logs"
    # 应用日志 / 操作登录日志默认保留天数
    log_retention_days: int = 7
    # 操作日志 / 登录日志保留天数；0 = 跟随 LOG_RETENTION_DAYS
    audit_log_retention_days: int = 0

    jwt_secret_key: str = "change-me-in-production-use-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    storage_root: str = "./storage"
    kb_upload_max_bytes: int = 200 * 1024 * 1024
    # 知识库视频单独上限（见 docs/kb-video-contract.md）
    kb_video_upload_max_bytes: int = 512 * 1024 * 1024
    kb_video_asr_timeout_seconds: int = 7200
    kb_extract_max_chars: int = 2_000_000

    # 知识库视频 ASR（OpenAI 兼容 /v1/audio/transcriptions；也可指本地 Whisper HTTP）
    # none = 不转写，视频上传会失败在入库阶段
    asr_provider: str = "none"
    asr_api_base: str = ""
    asr_api_key: str = ""
    asr_model: str = "whisper-1"
    asr_language: str = "zh"

    # 知识库 OCR：测试阶段 aliyun 通用文字识别（不是 DocMind）
    # none = 不调云，扫描页/图片会失败
    ocr_provider: str = "none"
    ocr_endpoint: str = "ocr-api.cn-hangzhou.aliyuncs.com"
    ocr_access_key_id: str = ""
    ocr_access_key_secret: str = ""
    ocr_type: str = "Advanced"
    ocr_timeout_seconds: int = 60
    # 文档智能 PDF 转 Word。密钥可与 OCR 共用；空则回退 OCR_ACCESS_KEY_*
    docmind_endpoint: str = "docmind-api.cn-hangzhou.aliyuncs.com"
    docmind_access_key_id: str = ""
    docmind_access_key_secret: str = ""
    # basic=新链路；keep_format=旧链路，版式更紧时再改
    docmind_pdf_option: str = "basic"
    docmind_timeout_seconds: int = 360
    ocr_max_pages: int = 800
    ocr_page_text_min_chars: int = 50
    # 后台解析卡住超过该秒数，启动时标为 failed，可点重试
    kb_parse_timeout_seconds: int = 7200

    # MCP stdio 仓库根（可选）。不设则自动探测 scripts/mcp_utility_server.py。
    # 部署非标准目录时可设：WEITAI_ROOT=/opt/weitaiAgent
    weitai_root: str = ""
    # 公司标准图框 DXF（可选）。空则用 assets/cad/title_a3.dxf，再没有则程序画标题栏。
    cad_title_block_dxf: str = ""
    # ODA / Teigha File Converter 可执行文件。空则按常见安装路径探测；找不到就不出 DWG。
    cad_oda_converter: str = ""

    # Embedding 调用批大小 / 单条截断（知识库向量化）
    embedding_batch_size: int = 8
    embedding_max_chars: int = 6000
    # 向量 Redis 缓存 TTL（embed:cache:{sha256}）
    chat_embed_cache_ttl_seconds: int = 7 * 24 * 3600

    qdrant_url: str = "http://127.0.0.1:16333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "weitai_knowledge"
    # none / int8 / binary — 仅新建集合时生效；已有集合用系统设置「应用」重建
    qdrant_quantization: str = "none"

    kb_chunk_size: int = 600
    kb_chunk_overlap: int = 150
    kb_search_top_k: int = 5
    kb_search_min_score: float = 0.0
    # 向量召回候选倍数，再按关键词重排截断为 top_k
    kb_search_candidate_multiplier: int = 6
    # 混合分 = (1-w)*向量分 + w*关键词分
    kb_search_keyword_weight: float = 0.5
    # 命中块前后各扩几块（补条款上下文）
    kb_neighbor_window: int = 1
    # 丢掉相对 top1 过弱的片段
    kb_score_floor: float = 0.12
    kb_keep_ratio: float = 0.38

    # 1/true/on：投标演示。上传和识别只走样子，下载用 storage/tender-demo 里的成稿。
    # 空或 0：仍走真实读取和组卷。改匹配代码时保持关闭。
    tender_demo: str = ""

    # ---- 在线 Word 改稿引擎 ----
    # browser=前端 docx-preview（无文档服务时的只读预览）
    # onlyoffice=OnlyOffice Document Server
    # yozo=永中 Web Office 内网私有化（远程文档 + 保存回调）
    tender_doc_preview: str = "browser"
    # 只读预览：用本机 WPS/Word/LibreOffice 把 docx 打成 PDF，版式对齐本地打开
    # auto=按 WPS → Word → LibreOffice 试；off=仍用浏览器内核
    tender_preview_converter: str = "auto"

    # ---- OnlyOffice 在线 Word ----
    # 浏览器加载 api.js：显式环境变量优先，否则使用当前局域网 IP；探测失败时单机回环兜底。
    onlyoffice_document_server_url: str = Field(
        default_factory=_default_onlyoffice_document_server_url
    )
    onlyoffice_jwt_secret: str = "onlyoffice-dev-secret-change-me"
    # 文档服务拉取 docx / 回调保存用的 API 根（Windows/Mac Docker 默认 host.docker.internal）
    onlyoffice_public_api_base: str = "http://host.docker.internal:8100"
    onlyoffice_download_token_ttl_seconds: int = 3600

    # ---- 永中 Web Office（内网自建）----
    # 浏览器访问的永中服务根，例如 http://192.168.1.10:8080
    yozo_document_server_url: str = ""
    # 开档页面路径。私有化 3.x 常见为 / 或 /index.html，以测试包为准
    yozo_open_path: str = "/"
    # query=把参数拼进 URL；json=使用 jsonParams（部分 3.x 版本）
    yozo_open_style: str = "query"
    # 永中服务器访问本 API 的根。空则沿用 ONLYOFFICE_PUBLIC_API_BASE
    yozo_public_api_base: str = ""
    # 可选：私有化若仍要 HMAC 验签则填写；纯远程文档可不填
    yozo_app_id: str = ""
    yozo_app_key: str = ""
    # 可选：从永中再下载新版本时的 API 根（云编辑 DMC，或私有化等价地址）
    yozo_api_base: str = ""

    @property
    def doc_preview_engine(self) -> str:
        raw = (self.tender_doc_preview or "browser").strip().lower()
        if raw in {"yozo", "yozosoft", "yozowo", "weboffice", "yz"}:
            if self.yozo_document_server_url.strip():
                return "yozo"
            return "browser"
        if raw in {"onlyoffice", "oo", "office"} and self.onlyoffice_document_server_url.strip():
            return "onlyoffice"
        return "browser"

    @property
    def cors_origin_list(self) -> list[str]:
        """解析 CORS allow_origins。

        - 空 / ``*`` → 允许任意 Origin（credentials=True 时 Starlette 回显 Origin）
        - 逗号分隔列表 → 白名单（生产建议显式配置）
        """
        raw = (self.cors_origins or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


@lru_cache
def get_settings() -> Settings:
    """进程内单例。测试或热更新配置时需 ``get_settings.cache_clear()``。"""
    return Settings()
