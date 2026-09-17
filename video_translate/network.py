"""Bounded HTTPS requests. Never expose response bodies or authorization headers."""
import time
import requests


class ProviderError(RuntimeError):
    pass


def request(method, url, *, session=None, attempts=3, **kwargs):
    client = session or requests
    for attempt in range(attempts):
        try:
            response = client.request(method, url, timeout=(10, 120), verify=True,
                                      allow_redirects=False, **kwargs)
        except requests.RequestException:
            if attempt + 1 == attempts:
                raise ProviderError("NETWORK_REQUEST_FAILED") from None
        else:
            if 200 <= response.status_code < 300:
                return response
            if response.status_code not in (408, 429) and response.status_code < 500:
                raise ProviderError(f"REMOTE_HTTP_{response.status_code}")
            if attempt + 1 == attempts:
                raise ProviderError(f"REMOTE_HTTP_{response.status_code}")
        time.sleep(min(2 ** attempt, 4))


def json_response(response):
    try:
        return response.json()
    except ValueError:
        raise ProviderError("INVALID_REMOTE_JSON") from None
