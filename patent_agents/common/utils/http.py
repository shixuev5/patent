import time

import requests
from loguru import logger
from requests import Response
from requests.exceptions import RequestException


def request_with_retry(
    method: str,
    url: str,
    *,
    attempts: int = 3,
    backoff_seconds: float = 2.0,
    log_prefix: str = "[HTTP]",
    **kwargs,
) -> Response:
    last_error: RequestException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return requests.request(method, url, **kwargs)
        except RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            logger.warning(
                f"{log_prefix} 请求失败，准备重试 {attempt}/{attempts}: {method.upper()} {url} -> {exc}"
            )
            time.sleep(backoff_seconds * attempt)

    if last_error is not None:
        raise last_error
    raise RuntimeError("unreachable")
