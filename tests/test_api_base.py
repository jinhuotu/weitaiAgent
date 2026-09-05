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


def test_lan_host_without_scheme_defaults_to_http() -> None:
    from api.services.models.urls import is_loopback_api_base

    assert normalize_api_base("http://192.168.2.112/:8001/v1") == "http://192.168.2.112:8001/v1"
    assert normalize_api_base("http://192.168.2.112:/8001/v1") == "http://192.168.2.112:8001/v1"
    assert normalize_api_base("10.0.0.8:11434/v1") == "http://10.0.0.8:11434/v1"
    assert is_loopback_api_base("http://127.0.0.1:8001/v1")
    assert not is_loopback_api_base("http://192.168.2.50:8001/v1")


def test_llm_allows_empty_api_key() -> None:
    from api.services.ai.llm import LLMClient

    client = LLMClient(
        api_base="http://192.168.2.50:8001/v1",
        api_key="",
        model="qwen2.5-14b-instruct",
    )
    assert "Authorization" not in client._headers()
    assert client._headers()["Content-Type"] == "application/json"


def test_llm_connection_error_explains_loopback() -> None:
    from api.services.ai.llm import _http_error_message

    text = _http_error_message(
        RuntimeError("All connection attempts failed"),
        api_base="http://127.0.0.1:8001/v1",
    )
    assert "127.0.0.1" in text
    assert "局域网" in text


def test_llm_connection_error_without_loopback() -> None:
    from api.services.ai.llm import _http_error_message

    text = _http_error_message(
        RuntimeError("All connection attempts failed"),
        api_base="http://192.168.2.50:8001/v1",
    )
    assert "连接被拒绝或不可达" in text


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


def test_llm_status_error_maps_unauthorized() -> None:
    from api.services.ai.llm import _status_error_message

    text = _status_error_message(401, "Unauthorized")
    assert "鉴权失败" in text
    assert text != "Unauthorized"
