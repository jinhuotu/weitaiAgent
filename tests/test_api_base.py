"""规范化模型 API Base。"""

from api.services.models.runtime import is_image_generation_model
from api.services.models.urls import normalize_api_base


def test_strips_double_https_and_dashscope_generation_path() -> None:
    raw = (
        "https://https://llm-pb7evr4e2i1id417.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert (
        normalize_api_base(raw)
        == "https://llm-pb7evr4e2i1id417.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )


def test_keeps_openai_v1() -> None:
    assert normalize_api_base("https://api.openai.com/v1/") == "https://api.openai.com/v1"


def test_strips_chat_completions_suffix() -> None:
    assert (
        normalize_api_base("https://api.openai.com/v1/chat/completions")
        == "https://api.openai.com/v1"
    )


def test_http_plain_host() -> None:
    assert normalize_api_base("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1"


def test_image_generation_model_names() -> None:
    assert is_image_generation_model("wan2.7-image-pro")
    assert is_image_generation_model("wanx-v1")
    assert not is_image_generation_model("qwen3.7-plus")
    assert not is_image_generation_model("qwen-vl-max")


def test_access_token_from_headers_bearer_and_fallback() -> None:
    from api.middleware.auth import access_token_from_headers

    assert access_token_from_headers({"Authorization": "Bearer abc"}) == "abc"
    assert access_token_from_headers({"X-Access-Token": "xyz"}) == "xyz"
    assert access_token_from_headers({"authorization": "Bearer a", "x-access-token": "b"}) == "a"
    assert access_token_from_headers({}) == ""
