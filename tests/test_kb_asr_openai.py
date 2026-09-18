import httpx

from api.services.knowledge.asr.openai_compat import OpenAiCompatAsrProvider


class _FakeResp:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"text":"你好"}'

    def json(self):
        return {"text": "你好", "segments": []}


def test_transcribe_sends_dict_data_with_files(tmp_path, monkeypatch) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"ID3fake")
    seen: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            seen["trust_env"] = kwargs.get("trust_env")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, data=None, files=None):
            seen["url"] = url
            seen["data"] = data
            seen["files"] = files
            return _FakeResp()

    monkeypatch.setattr(httpx, "Client", _Client)
    p = OpenAiCompatAsrProvider(
        api_base="http://127.0.0.1:19000/v1",
        api_key="k",
        model="base",
        language="zh",
        timeout_seconds=30,
    )
    out = p.transcribe(audio, filename="下载.mp4")
    assert out.text == "你好"
    assert isinstance(seen["data"], dict)
    assert seen["data"]["model"] == "base"
    assert seen["data"]["timestamp_granularities[]"] == "segment"
    assert seen["trust_env"] is False
    assert seen["url"].endswith("/audio/transcriptions")
