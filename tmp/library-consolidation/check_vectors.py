import sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd() / 'apps'), str(Path.cwd() / 'packages')]
from common.config import get_settings
import httpx
from urllib.parse import urlsplit

settings = get_settings()
parsed = urlsplit(settings.qdrant_url)
print('configured endpoint host=', parsed.hostname, 'port=', parsed.port)
try:
    headers = {'api-key': settings.qdrant_api_key} if settings.qdrant_api_key else {}
    response = httpx.get(settings.qdrant_url.rstrip('/') + '/collections', headers=headers, timeout=8, trust_env=False)
    print('direct health status=', response.status_code)
except Exception as exc:
    print('direct health failed=', type(exc).__name__)
