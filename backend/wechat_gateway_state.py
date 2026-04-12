from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Optional

from backend.time_utils import utc_now


@dataclass
class WeChatGatewayLoginState:
    status: str = "offline"
    qr_url: Optional[str] = None
    error_message: Optional[str] = None
    updated_at: Optional[str] = None


_state = WeChatGatewayLoginState()
_lock = Lock()


def update_wechat_gateway_login_state(
    *,
    status: str,
    qr_url: Optional[str] = None,
    error_message: Optional[str] = None,
) -> WeChatGatewayLoginState:
    with _lock:
        _state.status = str(status or "offline").strip() or "offline"
        _state.qr_url = str(qr_url or "").strip() or None
        _state.error_message = str(error_message or "").strip() or None
        _state.updated_at = utc_now().isoformat()
        return WeChatGatewayLoginState(
            status=_state.status,
            qr_url=_state.qr_url,
            error_message=_state.error_message,
            updated_at=_state.updated_at,
        )


def get_wechat_gateway_login_state() -> WeChatGatewayLoginState:
    with _lock:
        return WeChatGatewayLoginState(
            status=_state.status,
            qr_url=_state.qr_url,
            error_message=_state.error_message,
            updated_at=_state.updated_at,
        )
