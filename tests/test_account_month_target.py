from __future__ import annotations

import asyncio
import importlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.models import (
    AccountMonthTargetUpsertRequest,
    AccountNotificationSettingsUpdateRequest,
    AccountProfileUpdateRequest,
    AccountWeChatIntegrationUpdateRequest,
    InternalWeChatDeliveryJobClaimRequest,
    InternalWeChatDeliveryJobResolveRequest,
    InternalWeChatInboundAttachment,
    InternalWeChatLoginSessionStateUpdateRequest,
    CurrentUser,
)
from backend.notifications.task_wechat_service import TaskWeChatNotificationService
from backend.routes import account
from backend.routes import tasks as task_routes
from backend.storage import Task, TaskStatus, TaskType, User, WeChatBinding, WeChatConversationSession, WeChatDeliveryJob
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage
from backend.time_utils import utc_now
from backend.wechat_runtime import WeChatRuntimeService
from config import settings
from fastapi import HTTPException

WECHAT_INTENT_GUIDANCE = (
    '请直接说你的目标，我可以处理：AI 检索、专利分析、专利审查、审查意见答复。\n'
    '示例：帮我检索固态电池隔膜相关专利 / 分析专利 CN117347385A / 帮我审查这个专利 / 我要答复审查意见。'
)


def _build_ai_search_snapshot(*, session_id: str, title: str = 'AI 检索会话 - test', pending_action=None):
    return SimpleNamespace(
        session=SimpleNamespace(title=title),
        run={'status': 'pending', 'planVersion': 1},
        conversation={
            'messages': [{'role': 'assistant', 'content': '这是当前检索上下文'}],
            'pendingAction': pending_action,
        },
        plan={'currentPlan': {'planVersion': 1}},
        retrieval={'documents': {'candidates': [], 'selected': []}},
        artifacts={},
    )


async def _empty_async_iter():
    if False:
        yield None


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'month_target_test.db')
    monkeypatch.setattr(account, 'task_manager', SimpleNamespace(storage=storage))
    return storage


def test_month_target_carry_forward_chain(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='u-1')

    empty = asyncio.run(account.get_account_month_target(year=2026, month=3, current_user=user))
    assert empty.targetCount == 0
    assert empty.source == 'empty'

    march_saved = asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=3, targetCount=10),
            current_user=user,
        )
    )
    assert march_saved.targetCount == 10
    assert march_saved.source == 'explicit'

    april = asyncio.run(account.get_account_month_target(year=2026, month=4, current_user=user))
    assert april.targetCount == 10
    assert april.source == 'carried'

    asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=6, targetCount=12),
            current_user=user,
        )
    )
    july = asyncio.run(account.get_account_month_target(year=2026, month=7, current_user=user))
    assert july.targetCount == 12
    assert july.source == 'carried'


def test_dashboard_includes_target_and_source(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='u-2')

    asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=2, targetCount=8),
            current_user=user,
        )
    )

    dashboard = asyncio.run(account.get_account_dashboard(year=2026, month=3, current_user=user))
    assert dashboard.monthTarget == 8
    assert dashboard.monthTargetSource == 'carried'
    assert dashboard.year == 2026
    assert dashboard.month == 3


def test_profile_sanitizes_none_like_text(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:sub-1')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='sub-1',
            name='None',
            nickname='null',
            email='  user@example.com  ',
            phone='undefined',
            picture='',
        )
    )

    profile = asyncio.run(account.get_account_profile(current_user=user))
    assert profile.name is None
    assert profile.nickname is None
    assert profile.phone is None
    assert profile.picture is None
    assert profile.email == 'user@example.com'


def test_put_account_profile_updates_name_and_picture(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:sub-2')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='sub-2',
            name='旧名字',
            nickname='旧昵称',
            email='old@example.com',
            picture='https://unit.test/api/account/profile/avatar/local/old.png',
        )
    )

    updated = asyncio.run(
        account.put_account_profile(
            payload=AccountProfileUpdateRequest(
                name='新名字',
                picture='https://unit.test/api/account/profile/avatar/local/new.png',
            ),
            current_user=user,
        )
    )
    assert updated.name == '新名字'
    assert updated.picture == 'https://unit.test/api/account/profile/avatar/local/new.png'

    fetched = storage.get_user_by_owner_id(user.user_id)
    assert fetched is not None
    assert fetched.name == '新名字'
    assert fetched.picture == 'https://unit.test/api/account/profile/avatar/local/new.png'


def test_put_account_profile_rejects_empty_name(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:empty-name-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='empty-name-user',
            name='旧名字',
            email='empty@example.com',
            picture='https://example.com/old.png',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_profile(
                payload=AccountProfileUpdateRequest(
                    name='',
                    picture='https://example.com/new.png',
                ),
                current_user=user,
            )
        )
    assert exc_info.value.status_code == 400


def test_put_account_profile_rejects_duplicate_name(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user_a = CurrentUser(user_id='authing:user-a')
    user_b = CurrentUser(user_id='authing:user-b')
    storage.upsert_authing_user(
        User(
            owner_id=user_a.user_id,
            authing_sub='user-a',
            name='重复名',
            email='a@example.com',
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id=user_b.user_id,
            authing_sub='user-b',
            name='原名称',
            email='b@example.com',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_profile(
                payload=AccountProfileUpdateRequest(
                    name='重复名',
                    picture='https://example.com/new.png',
                ),
                current_user=user_b,
            )
        )
    assert exc_info.value.status_code == 409


def test_get_account_notification_settings_requires_authing_user(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(account.get_account_notification_settings(current_user=CurrentUser(user_id='guest-user')))

    assert exc_info.value.status_code == 403


def test_account_notification_settings_roundtrip(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:notify-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='notify-user',
            name='通知用户',
            email='login@example.com',
        )
    )

    initial = asyncio.run(account.get_account_notification_settings(current_user=user))
    assert initial.notificationEmailEnabled is False
    assert initial.workNotificationEmail is None
    assert initial.personalNotificationEmail is None

    saved = asyncio.run(
        account.put_account_notification_settings(
            payload=AccountNotificationSettingsUpdateRequest(
                notificationEmailEnabled=True,
                workNotificationEmail='worker@example.com',
                personalNotificationEmail='home@example.com',
            ),
            current_user=user,
        )
    )
    assert saved.notificationEmailEnabled is True
    assert saved.workNotificationEmail == 'worker@example.com'
    assert saved.personalNotificationEmail == 'home@example.com'

    fetched = storage.get_user_by_owner_id(user.user_id)
    assert fetched is not None
    assert fetched.notification_email_enabled is True
    assert fetched.work_notification_email == 'worker@example.com'
    assert fetched.personal_notification_email == 'home@example.com'


def test_account_notification_settings_require_at_least_one_email_when_enabled(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:no-email-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='no-email-user',
            name='无邮箱用户',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_notification_settings(
                payload=AccountNotificationSettingsUpdateRequest(
                    notificationEmailEnabled=True,
                    workNotificationEmail='',
                    personalNotificationEmail='',
                ),
                current_user=user,
            )
        )

    assert exc_info.value.status_code == 400


def test_account_notification_settings_reject_invalid_email(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:bad-email-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='bad-email-user',
            name='坏邮箱用户',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_notification_settings(
                payload=AccountNotificationSettingsUpdateRequest(
                    notificationEmailEnabled=True,
                    workNotificationEmail='not-an-email',
                    personalNotificationEmail='',
                ),
                current_user=user,
            )
        )

    assert exc_info.value.status_code == 400


def test_account_notification_settings_allow_duplicate_addresses(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:dup-email-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='dup-email-user',
            name='重复邮箱用户',
        )
    )

    saved = asyncio.run(
        account.put_account_notification_settings(
            payload=AccountNotificationSettingsUpdateRequest(
                notificationEmailEnabled=True,
                workNotificationEmail='same@example.com',
                personalNotificationEmail='same@example.com',
            ),
            current_user=user,
        )
    )

    assert saved.workNotificationEmail == 'same@example.com'
    assert saved.personalNotificationEmail == 'same@example.com'


def test_account_wechat_integration_bind_settings_and_disconnect(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'WECHAT_INTEGRATION_ENABLED', True)
    monkeypatch.setattr(settings, 'INTERNAL_GATEWAY_TOKEN', 'internal-test-token')
    user = CurrentUser(user_id='authing:wechat-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wechat-user',
            name='微信用户',
            email='wechat@example.com',
        )
    )

    initial = asyncio.run(account.get_account_wechat_integration(current_user=user))
    assert initial.bindingStatus == 'unbound'

    login_session = asyncio.run(account.post_account_wechat_login_session(current_user=user))
    assert login_session.status == 'pending'
    assert login_session.loginSessionId
    assert login_session.qrSvg == ''
    assert login_session.qrUrl is None

    qr_ready = asyncio.run(
        account.post_internal_wechat_login_session_state(
            login_session_id=login_session.loginSessionId,
            payload=InternalWeChatLoginSessionStateUpdateRequest(
                status='qr_ready',
                qrUrl='https://liteapp.weixin.qq.com/q/test-login-001',
            ),
            _token='internal-test-token',
        )
    )
    assert qr_ready['loginSession']['status'] == 'qr_ready'
    assert qr_ready['loginSession']['qrUrl'] == 'https://liteapp.weixin.qq.com/q/test-login-001'

    completed = asyncio.run(
        account.post_internal_wechat_login_session_state(
            login_session_id=login_session.loginSessionId,
            payload=InternalWeChatLoginSessionStateUpdateRequest(
                status='online',
                accountId='bot-1',
                wechatUserId='wx-user-001',
                wechatDisplayName='测试微信',
            ),
            _token='internal-test-token',
        )
    )
    assert completed['binding']['wechatDisplayName'] == '测试微信'

    integrated = asyncio.run(account.get_account_wechat_integration(current_user=user))
    assert integrated.bindingStatus == 'bound'
    assert integrated.binding is not None
    assert integrated.binding.wechatUserIdMasked is not None
    assert integrated.binding.accountId == 'bot-1'
    assert integrated.loginSession is not None
    assert integrated.loginSession.status == 'online'

    updated = asyncio.run(
        account.put_account_wechat_integration_settings(
            payload=AccountWeChatIntegrationUpdateRequest(
                pushTaskCompleted=False,
                pushTaskFailed=True,
                pushAiSearchPendingAction=False,
            ),
            current_user=user,
        )
    )
    assert updated.binding is not None
    assert updated.binding.pushTaskCompleted is False
    assert updated.binding.pushTaskFailed is True
    assert updated.binding.pushAiSearchPendingAction is False

    disconnected = asyncio.run(account.post_account_wechat_disconnect(current_user=user))
    assert disconnected.bindingStatus == 'unbound'


def test_account_wechat_bind_session_refreshes_pending_session(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'WECHAT_INTEGRATION_ENABLED', True)
    user = CurrentUser(user_id='authing:wechat-refresh-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wechat-refresh-user',
            name='刷新二维码用户',
            email='wechat-refresh@example.com',
        )
    )

    first = asyncio.run(account.post_account_wechat_login_session(current_user=user))
    second = asyncio.run(account.post_account_wechat_login_session(current_user=user))

    assert first.loginSessionId != second.loginSessionId
    cancelled = storage.get_wechat_login_session(first.loginSessionId)
    assert cancelled is not None
    assert cancelled.status == 'cancelled'
    assert second.status == 'pending'


def test_account_wechat_login_session_terminal_state_update_is_ignored(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'WECHAT_INTEGRATION_ENABLED', True)
    monkeypatch.setattr(settings, 'INTERNAL_GATEWAY_TOKEN', 'internal-test-token')
    user = CurrentUser(user_id='authing:wechat-terminal-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wechat-terminal-user',
            name='终态登录会话用户',
            email='wechat-terminal@example.com',
        )
    )

    login_session = asyncio.run(account.post_account_wechat_login_session(current_user=user))
    expired = asyncio.run(
        account.post_internal_wechat_login_session_state(
            login_session_id=login_session.loginSessionId,
            payload=InternalWeChatLoginSessionStateUpdateRequest(status='expired'),
            _token='internal-test-token',
        )
    )
    assert expired['loginSession']['status'] == 'expired'

    ignored = asyncio.run(
        account.post_internal_wechat_login_session_state(
            login_session_id=login_session.loginSessionId,
            payload=InternalWeChatLoginSessionStateUpdateRequest(
                status='qr_ready',
                qrUrl='https://liteapp.weixin.qq.com/q/should-be-ignored',
            ),
            _token='internal-test-token',
        )
    )
    assert ignored['ignored'] is True
    assert ignored['loginSession']['status'] == 'expired'
    assert ignored['loginSession']['qrUrl'] is None


def test_account_wechat_login_qr_svg_depends_on_qr_url():
    first = account._render_qr_svg('https://liteapp.weixin.qq.com/q/test-001')
    second = account._render_qr_svg('https://liteapp.weixin.qq.com/q/test-002')

    assert first.startswith('<svg')
    assert '<path' in first
    assert first != second


def test_account_wechat_login_session_is_owner_scoped(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'WECHAT_INTEGRATION_ENABLED', True)
    monkeypatch.setattr(settings, 'INTERNAL_GATEWAY_TOKEN', 'internal-test-token')

    user = CurrentUser(user_id='authing:wechat-gateway-qr-user')
    other_user = CurrentUser(user_id='authing:wechat-gateway-qr-user-2')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wechat-gateway-qr-user',
            name='网关二维码用户',
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id=other_user.user_id,
            authing_sub='wechat-gateway-qr-user-2',
            name='另一个二维码用户',
        )
    )

    first = asyncio.run(account.post_account_wechat_login_session(current_user=user))
    second = asyncio.run(account.post_account_wechat_login_session(current_user=other_user))
    asyncio.run(
        account.post_internal_wechat_login_session_state(
            login_session_id=first.loginSessionId,
            payload=InternalWeChatLoginSessionStateUpdateRequest(
                status='qr_ready',
                qrUrl='https://liteapp.weixin.qq.com/q/test-owner-1',
            ),
            _token='internal-test-token',
        )
    )

    refreshed_first = asyncio.run(account.get_account_wechat_login_session(first.loginSessionId, current_user=user))
    refreshed_second = asyncio.run(account.get_account_wechat_login_session(second.loginSessionId, current_user=other_user))

    assert refreshed_first.qrUrl == 'https://liteapp.weixin.qq.com/q/test-owner-1'
    assert refreshed_first.qrSvg.startswith('<svg')
    assert refreshed_second.qrUrl is None
    assert refreshed_second.qrSvg == ''


def test_account_wechat_login_replaces_existing_binding(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'WECHAT_INTEGRATION_ENABLED', True)
    monkeypatch.setattr(settings, 'INTERNAL_GATEWAY_TOKEN', 'internal-test-token')
    user = CurrentUser(user_id='authing:wechat-code-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wechat-code-user',
            name='微信码绑定用户',
            email='wechat-code@example.com',
        )
    )

    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='wb-existing',
            owner_id=user.user_id,
            status='active',
            bot_account_id='bot-old',
            wechat_user_id='wx-old',
            wechat_display_name='旧账号',
        )
    )

    login_session = asyncio.run(account.post_account_wechat_login_session(current_user=user))
    assert storage.get_wechat_binding_by_owner(user.user_id) is None

    completed = asyncio.run(
        account.post_internal_wechat_login_session_state(
            login_session_id=login_session.loginSessionId,
            payload=InternalWeChatLoginSessionStateUpdateRequest(
                status='online',
                accountId='bot-new',
                wechatUserId='wx-new',
                wechatDisplayName='新账号',
            ),
            _token='internal-test-token',
        )
    )
    assert completed['binding']['accountId'] == 'bot-new'
    assert completed['binding']['wechatDisplayName'] == '新账号'
    integrated = asyncio.run(account.get_account_wechat_integration(current_user=user))
    assert integrated.bindingStatus == 'bound'
    assert integrated.binding is not None
    assert integrated.binding.accountId == 'bot-new'


def _load_im_gateway_main():
    module = importlib.import_module('im_gateway.main')
    return importlib.reload(module)


def test_im_gateway_module_entrypoint_uses_package_imports():
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env['INTERNAL_GATEWAY_TOKEN'] = ''
    result = subprocess.run(
        [sys.executable, '-m', 'im_gateway.main'],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert '缺少 INTERNAL_GATEWAY_TOKEN' in result.stderr
    assert "No module named 'backend'" not in result.stderr


def test_im_gateway_r2_credential_store_roundtrip(tmp_path):
    im_gateway_main = _load_im_gateway_main()

    class FakeR2Storage:
        def __init__(self):
            self.payload: bytes | None = None
            self.deleted_keys: list[str] = []
            self.get_bytes_calls: list[tuple[str, bool]] = []

        def get_bytes(self, key: str, *, log_missing: bool = True):
            assert key == 'secure/im-gateway/credentials.enc'
            self.get_bytes_calls.append((key, log_missing))
            return self.payload

        def put_bytes(self, key: str, content: bytes, content_type: str = 'application/octet-stream'):
            assert key == 'secure/im-gateway/credentials.enc'
            assert content_type == 'application/octet-stream'
            self.payload = content
            return True

        def delete_key(self, key: str):
            self.deleted_keys.append(key)
            self.payload = None
            return True

    local_path = tmp_path / 'credentials.json'
    expected = b'{"token":"t-001","accountId":"bot-001"}\n'
    local_path.write_bytes(expected)

    r2_storage = FakeR2Storage()
    store = im_gateway_main.R2CredentialStore(
        r2_storage=r2_storage,
        r2_key='secure/im-gateway/credentials.enc',
        local_path=local_path,
        encryption_secret='secret-for-test',
    )

    assert store.persist_local_credentials() is True
    assert r2_storage.payload is not None
    assert r2_storage.payload != expected

    local_path.unlink()
    assert store.restore_local_credentials() is True
    assert local_path.read_bytes() == expected
    assert r2_storage.get_bytes_calls == [('secure/im-gateway/credentials.enc', False)]

    assert store.clear_remote_credentials() is True
    assert r2_storage.deleted_keys == ['secure/im-gateway/credentials.enc']


def test_account_runtime_clear_credentials_clears_local_and_remote(monkeypatch, tmp_path):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    events: list[str] = []

    class FakeCredentialStore:
        local_path = tmp_path / 'credentials.json'

        def clear_local_credentials(self):
            events.append('clear-local')
            return True

        def clear_remote_credentials(self):
            events.append('clear-remote')
            return True

        def has_local_credentials(self):
            return False

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-1',
        backend=SimpleNamespace(),
        download_dir=tmp_path,
        background_task_tracker=lambda _task: None,
    )
    runtime.credential_store = FakeCredentialStore()

    asyncio.run(runtime._clear_credentials())

    assert events == ['clear-local', 'clear-remote']


def test_account_runtime_logs_missing_credentials_only_once(monkeypatch, tmp_path):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(im_gateway_main, 'POLL_INTERVAL_SECONDS', 0.01)
    printed: list[str] = []
    attempts = 0

    class FakeBot:
        def on_message(self, _handler):
            return None

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-missing',
        backend=SimpleNamespace(),
        download_dir=tmp_path,
        background_task_tracker=lambda _task: None,
    )

    async def fake_ensure_credentials_for_restore():
        nonlocal attempts
        attempts += 1
        if attempts >= 3:
            runtime._stop_requested = True
        return False

    monkeypatch.setattr(runtime, '_build_bot', lambda: FakeBot())
    monkeypatch.setattr(runtime, '_ensure_credentials_for_restore', fake_ensure_credentials_for_restore)
    monkeypatch.setattr('builtins.print', lambda message: printed.append(message))

    asyncio.run(runtime._run_loop())

    assert attempts == 3
    assert printed == ['[im-gateway] owner=authing:owner-missing missing credentials, waiting for a new login session']


def test_account_runtime_reports_expired_login_session(monkeypatch, tmp_path):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    updates: list[dict[str, object]] = []
    background_tasks: list[asyncio.Task[object]] = []

    class FakeBackend:
        async def update_login_session_state(self, **payload):
            updates.append(payload)
            return {}

    class FakeBot:
        def on_message(self, _handler):
            return None

        async def login(self, *, force: bool = False):
            assert force is True
            runtime._on_qr_url('https://liteapp.weixin.qq.com/q/expired-login')
            await asyncio.sleep(0)
            runtime._on_expired()
            await asyncio.sleep(0)
            runtime._on_qr_url('https://liteapp.weixin.qq.com/q/late-qr-should-be-ignored')
            await asyncio.sleep(0)
            raise RuntimeError('QR code expired 3 times — login aborted')

        async def start(self):
            raise AssertionError('start should not run after login failure')

        async def stop(self):
            return None

    async def run_case():
        nonlocal runtime
        runtime = im_gateway_main.AccountRuntime(
            owner_id='authing:owner-expired',
            backend=FakeBackend(),
            download_dir=tmp_path,
            background_task_tracker=background_tasks.append,
        )
        monkeypatch.setattr(runtime, '_build_bot', lambda: FakeBot())
        monkeypatch.setattr(runtime, '_persist_credentials_to_remote', lambda: asyncio.sleep(0, result=False))

        await runtime.apply_target(
            im_gateway_main.OwnerRuntimeTarget(
                owner_id='authing:owner-expired',
                login_session_id='wls-expired-001',
            )
        )
        await asyncio.sleep(0.05)
        await runtime.stop(clear_credentials=False)
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    runtime = None
    asyncio.run(run_case())

    assert [item['status'] for item in updates] == ['qr_ready', 'expired']
    assert updates[0]['qr_url'] == 'https://liteapp.weixin.qq.com/q/expired-login'


def test_im_gateway_waits_for_backend_before_starting_poller(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    events: list[str] = []

    class FakeBackend:
        async def wait_until_ready(self):
            events.append('backend:ready')

        async def fetch_runtime_snapshot(self):
            events.append('snapshot:fetch')
            return {}

        async def close(self):
            events.append('backend:close')

    gateway = im_gateway_main.WeChatGateway(backend=FakeBackend())

    async def fake_poll_delivery_jobs():
        events.append('poller:start')
        await asyncio.Event().wait()

    async def fake_reconcile(snapshot):
        assert snapshot == {}
        events.append('reconcile')

    async def fake_sleep(seconds: int):
        if seconds == 0:
            return None
        raise asyncio.CancelledError()

    monkeypatch.setattr(gateway, '_poll_delivery_jobs', fake_poll_delivery_jobs)
    monkeypatch.setattr(gateway, '_reconcile', fake_reconcile)
    monkeypatch.setattr(im_gateway_main.asyncio, 'sleep', fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(gateway.run())

    assert events[0] == 'backend:ready'
    assert events.index('backend:ready') < events.index('snapshot:fetch')
    assert events.index('backend:ready') < events.index('reconcile')
    if 'poller:start' in events:
        assert events.index('backend:ready') < events.index('poller:start')


def test_im_gateway_reconciles_owner_scoped_sessions_and_bindings(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    applied: list[tuple[str, str | None, str | None]] = []
    stopped: list[tuple[str, bool]] = []

    class FakeRuntime:
        def __init__(self, *, owner_id: str, backend, download_dir, background_task_tracker):
            self.owner_id = owner_id

        async def apply_target(self, target):
            applied.append((self.owner_id, target.account_id, target.login_session_id))

        async def stop(self, *, clear_credentials: bool):
            stopped.append((self.owner_id, clear_credentials))

    monkeypatch.setattr(im_gateway_main, 'AccountRuntime', FakeRuntime)
    gateway = im_gateway_main.WeChatGateway(backend=SimpleNamespace())

    asyncio.run(
        gateway._reconcile(
            {
                'activeBindings': [
                    {'ownerId': 'authing:owner-a', 'accountId': 'bot-a'},
                ],
                'pendingLoginSessions': [
                    {'ownerId': 'authing:owner-b', 'loginSessionId': 'wls-b-001'},
                ],
            }
        )
    )
    asyncio.run(
        gateway._reconcile(
            {
                'activeBindings': [
                    {'ownerId': 'authing:owner-b', 'accountId': 'bot-b'},
                ],
                'pendingLoginSessions': [],
            }
        )
    )

    assert ('authing:owner-a', 'bot-a', None) in applied
    assert ('authing:owner-b', None, 'wls-b-001') in applied
    assert ('authing:owner-b', 'bot-b', None) in applied
    assert stopped == [('authing:owner-a', True)]


def test_im_gateway_uses_sdk_credentials_account_id(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    captured: dict[str, str] = {}

    class FakeBackend:
        async def post_inbound_message(self, **payload):
            captured['bot_account_id'] = payload['bot_account_id']
            return {'messages': []}

    class FakeBot:
        def get_credentials(self):
            return SimpleNamespace(account_id='bot-cred-001')

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-1',
        backend=FakeBackend(),
        download_dir=Path('.'),
        background_task_tracker=lambda _task: None,
    )

    asyncio.run(
        runtime._handle_message(
            FakeBot(),
            SimpleNamespace(
                user_id='wx-peer-001',
                text='hello',
            )
        )
    )

    assert captured['bot_account_id'] == 'bot-cred-001'


def test_im_gateway_wraps_file_payloads_for_sdk(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    reply_payloads: list[dict[str, object]] = []
    send_payloads: list[dict[str, object]] = []
    sent_texts: list[str] = []

    class FakeBackend:
        async def download_task_artifact(self, _download_path: str):
            return b'file-bytes', 'application/pdf', 'result.pdf'

    class FakeBot:
        async def send(self, _peer_id: str, text: str):
            sent_texts.append(text)

        async def send_media(self, _peer_id: str, payload: dict[str, object]):
            send_payloads.append(payload)

        async def reply_media(self, _incoming_msg, payload: dict[str, object]):
            reply_payloads.append(payload)

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-1',
        backend=FakeBackend(),
        download_dir=Path('.'),
        background_task_tracker=lambda _task: None,
    )
    runtime.bot = FakeBot()
    runtime.current_account_id = 'bot-001'

    asyncio.run(
        runtime._send_messages(
            bot=runtime.bot,
            peer_id='wx-peer-001',
            incoming_msg=SimpleNamespace(user_id='wx-peer-001'),
            messages=[{'type': 'file', 'downloadPath': '/download/path', 'fileName': 'custom.pdf'}],
        )
    )
    asyncio.run(
        runtime.send_delivery_job(
            {
                'deliveryJobId': 'job-001',
                'binding': {'accountId': 'bot-001', 'peerId': 'wx-peer-001'},
                'payload': {'title': '分析任务', 'terminalStatus': 'completed'},
                'task': {'downloadPath': '/download/path'},
            }
        )
    )

    assert reply_payloads == [{'file': b'file-bytes', 'file_name': 'custom.pdf'}]
    assert send_payloads == [{'file': b'file-bytes', 'file_name': 'result.pdf'}]
    assert sent_texts[0] == '分析任务 已完成。'


def test_im_gateway_formats_pending_action_delivery(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    sent_texts: list[str] = []

    class FakeBot:
        async def send(self, _peer_id: str, text: str):
            sent_texts.append(text)

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-1',
        backend=SimpleNamespace(),
        download_dir=Path('.'),
        background_task_tracker=lambda _task: None,
    )
    runtime.bot = FakeBot()
    runtime.current_account_id = 'bot-001'

    asyncio.run(
        runtime.send_delivery_job(
            {
                'deliveryJobId': 'job-question-001',
                'binding': {'accountId': 'bot-001', 'peerId': 'wx-peer-001'},
                'payload': {
                    'title': '隔膜检索',
                    'pendingActionType': 'question',
                    'prompt': '请补充核心技术特征',
                },
                'task': {},
            }
        )
    )

    assert sent_texts == ['隔膜检索 需要补充信息。\n请补充核心技术特征']


def test_account_runtime_sends_typing_indicator_when_supported(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    typing_calls: list[str] = []

    class FakeBackend:
        async def post_inbound_message(self, **payload):
            assert payload['bot_account_id'] == 'bot-cred-typing'
            return {'messages': []}

    class FakeBot:
        def get_credentials(self):
            return SimpleNamespace(account_id='bot-cred-typing')

        async def sendTyping(self, peer_id: str):
            typing_calls.append(peer_id)

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-typing',
        backend=FakeBackend(),
        download_dir=Path('.'),
        background_task_tracker=lambda _task: None,
    )

    asyncio.run(
        runtime._handle_message(
            FakeBot(),
            SimpleNamespace(
                user_id='wx-peer-typing',
                text='hello',
            )
        )
    )

    assert typing_calls == ['wx-peer-typing']


def test_account_runtime_reports_login_session_updates(monkeypatch, tmp_path):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    updates: list[dict[str, object]] = []
    background_tasks: list[asyncio.Task[object]] = []

    class FakeBackend:
        async def update_login_session_state(self, **payload):
            updates.append(payload)
            return {}

    class FakeBot:
        def on_message(self, _handler):
            return None

        async def login(self, *, force: bool = False):
            assert force is True
            runtime._on_qr_url('https://liteapp.weixin.qq.com/q/login-001')
            await asyncio.sleep(0)
            runtime._on_scanned()
            await asyncio.sleep(0)
            return SimpleNamespace(account_id='bot-001', user_id='wx-user-001')

        async def start(self):
            raise asyncio.CancelledError()

        async def stop(self):
            return None

    async def fake_persist():
        return True

    async def run_case():
        nonlocal runtime
        runtime = im_gateway_main.AccountRuntime(
            owner_id='authing:owner-login',
            backend=FakeBackend(),
            download_dir=tmp_path,
            background_task_tracker=background_tasks.append,
        )
        monkeypatch.setattr(runtime, '_build_bot', lambda: FakeBot())
        monkeypatch.setattr(runtime, '_persist_credentials_to_remote', fake_persist)
        await runtime.apply_target(
            im_gateway_main.OwnerRuntimeTarget(
                owner_id='authing:owner-login',
                login_session_id='wls-login-001',
            )
        )
        await asyncio.sleep(0.05)
        await runtime.stop(clear_credentials=False)
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    runtime = None
    asyncio.run(run_case())

    assert [item['status'] for item in updates] == ['qr_ready', 'scanned', 'online']
    assert updates[-1]['account_id'] == 'bot-001'
    assert updates[-1]['wechat_user_id'] == 'wx-user-001'


def test_im_gateway_sends_processing_hint_then_final_reply(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(im_gateway_main, 'INBOUND_REPLY_WAIT_SECONDS', 0.01)
    monkeypatch.setattr(im_gateway_main, 'INBOUND_REQUEST_TIMEOUT_SECONDS', 5.0)

    backend_started = asyncio.Event()
    allow_finish = asyncio.Event()
    replies: list[str] = []
    background_tasks: list[asyncio.Task[object]] = []

    class FakeBackend:
        async def post_inbound_message(self, **payload):
            backend_started.set()
            await allow_finish.wait()
            return {'messages': [{'type': 'text', 'text': '最终结果到了'}]}

    class FakeBot:
        async def reply(self, _incoming_msg, text: str):
            replies.append(text)

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-1',
        backend=FakeBackend(),
        download_dir=Path('.'),
        background_task_tracker=background_tasks.append,
    )
    bot = FakeBot()

    async def run_case():
        task = asyncio.create_task(
            runtime._handle_message(
                bot,
                SimpleNamespace(
                    user_id='wx-peer-001',
                    text='帮我检索固态电池隔膜相关专利',
                )
            )
        )
        await backend_started.wait()
        await asyncio.sleep(0.03)
        assert replies == ['收到，我正在整理这条消息，可能需要一点时间。你先不用重复发送，后续进展我会直接发到这里。']
        assert task.done()
        allow_finish.set()
        await asyncio.sleep(0.03)
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    asyncio.run(run_case())

    assert replies == [
        '收到，我正在整理这条消息，可能需要一点时间。你先不用重复发送，后续进展我会直接发到这里。',
        '最终结果到了',
    ]


def test_im_gateway_sends_timeout_hint_after_slow_background_failure(monkeypatch):
    im_gateway_main = _load_im_gateway_main()
    monkeypatch.delenv('IM_GATEWAY_CRED_R2_PREFIX', raising=False)
    monkeypatch.delenv('IM_GATEWAY_CRED_ENCRYPTION_KEY', raising=False)
    monkeypatch.setattr(im_gateway_main, 'INBOUND_REPLY_WAIT_SECONDS', 0.01)
    monkeypatch.setattr(im_gateway_main, 'INBOUND_REQUEST_TIMEOUT_SECONDS', 0.02)

    replies: list[str] = []
    background_tasks: list[asyncio.Task[object]] = []

    class FakeBackend:
        async def post_inbound_message(self, **payload):
            await asyncio.sleep(0.03)
            raise httpx.ReadTimeout('timed out')

    class FakeBot:
        async def reply(self, _incoming_msg, text: str):
            replies.append(text)

    runtime = im_gateway_main.AccountRuntime(
        owner_id='authing:owner-1',
        backend=FakeBackend(),
        download_dir=Path('.'),
        background_task_tracker=background_tasks.append,
    )
    bot = FakeBot()

    async def run_case():
        await runtime._handle_message(
            bot,
            SimpleNamespace(
                user_id='wx-peer-001',
                text='帮我检索固态电池隔膜相关专利',
            )
        )
        await asyncio.sleep(0.08)
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)

    asyncio.run(run_case())

    assert replies == [
        '收到，我正在整理这条消息，可能需要一点时间。你先不用重复发送，后续进展我会直接发到这里。',
        '抱歉，让你久等了。这条消息处理得比预期慢一些，我这边还没拿到最终结果。你先不用重复发送，我会继续留意后续进展。',
    ]


def test_wechat_terminal_notification_enqueues_and_claims_job(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, 'WECHAT_INTEGRATION_ENABLED', True)
    monkeypatch.setattr(settings, 'INTERNAL_GATEWAY_TOKEN', 'internal-test-token')
    user = CurrentUser(user_id='authing:wechat-notify')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wechat-notify',
            name='微信通知用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-1',
            owner_id=user.user_id,
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-002',
            wechat_peer_name='通知微信',
            bound_at=utc_now(),
        )
    )
    storage.create_task(
        Task(
            id='task-1',
            owner_id=user.user_id,
            task_type=TaskType.PATENT_ANALYSIS.value,
            title='分析任务',
            status=TaskStatus.COMPLETED,
            progress=100,
            output_dir=str(tmp_path / 'output' / 'task-1'),
            created_at=utc_now(),
            updated_at=utc_now(),
            metadata={},
        )
    )

    service = TaskWeChatNotificationService(storage=storage)
    queued = service.notify_task_terminal_status(
        'task-1',
        terminal_status='completed',
        task_type=TaskType.PATENT_ANALYSIS.value,
    )
    assert queued['status'] == 'queued'

    claimed = asyncio.run(
        account.post_internal_wechat_delivery_jobs_claim(
            payload=InternalWeChatDeliveryJobClaimRequest(limit=5),
            _token='internal-test-token',
        )
    )
    assert claimed['total'] == 1
    item = claimed['items'][0]
    assert item['taskId'] == 'task-1'
    assert item['payload']['terminalStatus'] == 'completed'

    asyncio.run(
        account.post_internal_wechat_delivery_job_complete(
            delivery_job_id=item['deliveryJobId'],
            _payload=InternalWeChatDeliveryJobResolveRequest(),
            _token='internal-test-token',
        )
    )
    jobs = storage.claim_wechat_delivery_jobs(5)
    assert jobs == []


def test_wechat_runtime_requires_active_binding_with_structured_detail(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_missing_binding.db')
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            service.handle_inbound_message(
                bot_account_id='bot-1',
                wechat_peer_id='wx-peer-missing',
                text='你好',
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail['code'] == 'WECHAT_BINDING_NOT_FOUND'
    assert '网页里完成绑定' in exc_info.value.detail['suggestion']


def test_task_wechat_pending_action_notification_queues_delivery_job(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_pending_action_delivery.db')
    user = CurrentUser(user_id='authing:wx-pending-action')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='wx-pending-action',
            name='待确认提醒用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-pending-action',
            owner_id=user.user_id,
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-pending',
            wechat_peer_name='待确认微信',
            bound_at=utc_now(),
            push_ai_search_pending_action=True,
        )
    )
    storage.create_task(
        Task(
            id='search-task-1',
            owner_id=user.user_id,
            task_type=TaskType.AI_SEARCH.value,
            title='固态电池隔膜检索',
            status=TaskStatus.PENDING,
            progress=40,
            output_dir=str(tmp_path / 'output' / 'search-task-1'),
            created_at=utc_now(),
            updated_at=utc_now(),
            metadata={},
        )
    )

    service = TaskWeChatNotificationService(storage=storage)
    queued = service.notify_ai_search_pending_action(
        'search-task-1',
        pending_action={
            'actionId': 'pa-001',
            'actionType': 'question',
            'prompt': '请补充核心技术特征',
        },
    )
    duplicate = service.notify_ai_search_pending_action(
        'search-task-1',
        pending_action={
            'actionId': 'pa-001',
            'actionType': 'question',
            'prompt': '请补充核心技术特征',
        },
    )

    assert queued['status'] == 'queued'
    assert duplicate['status'] == 'duplicate'

    jobs = storage.claim_wechat_delivery_jobs(5)
    assert len(jobs) == 1
    assert jobs[0].event_type == 'ai_search.pending_action'
    assert jobs[0].payload['pendingActionType'] == 'question'
    assert jobs[0].payload['prompt'] == '请补充核心技术特征'


def test_claim_wechat_delivery_jobs_skips_stale_rows_when_compare_and_claim_loses_race(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_delivery_claim_race.db')
    created = storage.create_wechat_delivery_job(
        WeChatDeliveryJob(
            delivery_job_id='job-race-1',
            owner_id='authing:wx-race',
            binding_id='binding-race',
            event_type='task.completed',
            status='pending',
            payload={'taskId': 'task-race-1'},
        )
    )
    storage.update_wechat_delivery_job(created.delivery_job_id, status='processing')

    original_fetchall = storage._fetchall

    def _stale_fetchall(sql, params=None):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("SELECT * FROM wechat_delivery_jobs WHERE status = 'pending'"):
            return [
                {
                    'delivery_job_id': created.delivery_job_id,
                    'owner_id': created.owner_id,
                    'binding_id': created.binding_id,
                    'task_id': created.task_id,
                    'event_type': created.event_type,
                    'status': 'pending',
                    'payload_json': '{"taskId":"task-race-1"}',
                    'attempt_count': 0,
                    'max_attempts': 3,
                    'next_attempt_at': None,
                    'claimed_at': None,
                    'completed_at': None,
                    'failed_at': None,
                    'last_error': None,
                    'created_at': created.created_at.isoformat(),
                    'updated_at': created.updated_at.isoformat(),
                }
            ]
        return original_fetchall(sql, params)

    monkeypatch.setattr(storage, "_fetchall", _stale_fetchall)

    jobs = storage.claim_wechat_delivery_jobs(1)

    assert jobs == []


def test_wechat_runtime_analysis_flow_creates_task(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_analysis.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-analysis',
            authing_sub='wx-analysis',
            name='微信分析用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-analysis',
            owner_id='authing:wx-analysis',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis',
            wechat_peer_name='分析微信',
            bound_at=utc_now(),
        )
    )
    monkeypatch.setattr(task_routes, '_enqueue_pipeline_task', lambda *args, **kwargs: None)
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.PATENT_ANALYSIS.value,
            'confidence': 0.93,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    started = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis',
            text='帮我分析这个专利',
        )
    )
    assert started.sessionType == TaskType.PATENT_ANALYSIS.value
    assert '专利号' in (started.messages[0].text or '')

    created = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis',
            text='CN202410001234.5',
        )
    )
    assert created.taskId
    task = storage.get_task(created.taskId)
    assert task is not None
    assert task.task_type == TaskType.PATENT_ANALYSIS.value
    assert task.pn == 'CN202410001234.5'


def test_wechat_runtime_natural_language_analysis_routes_to_task(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_intent_analysis.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-intent-analysis',
            authing_sub='wx-intent-analysis',
            name='微信意图分析用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-intent-analysis',
            owner_id='authing:wx-intent-analysis',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-intent-analysis',
            wechat_peer_name='意图分析微信',
            bound_at=utc_now(),
        )
    )
    monkeypatch.setattr(task_routes, '_enqueue_pipeline_task', lambda *args, **kwargs: None)
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'patent_analysis',
            'confidence': 0.92,
            'requires_confirmation': False,
            'extracted': {'patent_number': 'CN202410009999.9'},
        },
    )

    created = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-intent-analysis',
            text='帮我分析一下这个专利，专利号 CN202410009999.9',
        )
    )
    assert created.taskId
    task = storage.get_task(created.taskId)
    assert task is not None
    assert task.task_type == TaskType.PATENT_ANALYSIS.value
    assert task.pn == 'CN202410009999.9'


def test_wechat_runtime_intent_router_uses_supported_task_kind(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_task_kind.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-task-kind',
            authing_sub='wx-task-kind',
            name='微信任务类型用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-task-kind',
            owner_id='authing:wx-task-kind',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-task-kind',
            wechat_peer_name='任务类型微信',
            bound_at=utc_now(),
        )
    )
    monkeypatch.setattr(task_routes, '_enqueue_pipeline_task', lambda *args, **kwargs: None)
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))

    def _invoke_text_json(*args, **kwargs):
        assert kwargs['task_kind'] == 'wechat_intent_routing'
        return {
            'intent': 'patent_analysis',
            'confidence': 0.92,
            'requires_confirmation': False,
            'extracted': {'patent_number': 'CN202410008888.8'},
        }

    monkeypatch.setattr(service.llm_service, 'invoke_text_json', _invoke_text_json)

    created = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-task-kind',
            text='分析专利 CN202410008888.8',
        )
    )

    assert created.taskId
    task = storage.get_task(created.taskId)
    assert task is not None
    assert task.task_type == TaskType.PATENT_ANALYSIS.value
    assert task.pn == 'CN202410008888.8'


def test_wechat_runtime_low_confidence_intent_asks_for_confirmation(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_intent_uncertain.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-intent-uncertain',
            authing_sub='wx-intent-uncertain',
            name='微信意图不确定用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-intent-uncertain',
            owner_id='authing:wx-intent-uncertain',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-intent-uncertain',
            wechat_peer_name='意图不确定微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'patent_analysis',
            'confidence': 0.41,
            'requires_confirmation': True,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-intent-uncertain',
            text='这个帮我看一下',
        )
    )
    assert result.taskId is None
    assert (result.messages[0].text or '') == WECHAT_INTENT_GUIDANCE


def test_wechat_runtime_analysis_flow_natural_language_cancel_clears_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_analysis_flow_cancel_intent.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-analysis-flow-cancel',
            authing_sub='wx-analysis-flow-cancel',
            name='微信分析流程取消用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-analysis-flow-cancel',
            owner_id='authing:wx-analysis-flow-cancel',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis-flow-cancel',
            wechat_peer_name='分析流程取消微信',
            bound_at=utc_now(),
        )
    )
    storage.upsert_wechat_flow_session(
        'authing:wx-analysis-flow-cancel',
        TaskType.PATENT_ANALYSIS.value,
        current_step='await_patent_input',
        draft_payload={},
        expires_at=utc_now() + timedelta(hours=12),
    )
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-analysis-flow-cancel',
            owner_id='authing:wx-analysis-flow-cancel',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='guided_workflow',
            active_context_session_id='flow-analysis-flow-cancel',
            active_context_title='AI 分析',
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'cancel_or_pause',
            'confidence': 0.93,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis-flow-cancel',
            text='先别做了',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert '取消当前微信任务收集流程' in (result.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'none'
    assert storage.get_active_wechat_flow_session('authing:wx-analysis-flow-cancel', TaskType.PATENT_ANALYSIS.value) is None


def test_wechat_runtime_analysis_flow_chitchat_does_not_create_task(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_analysis_flow_chitchat.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-analysis-flow-chitchat',
            authing_sub='wx-analysis-flow-chitchat',
            name='微信分析流程闲聊用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-analysis-flow-chitchat',
            owner_id='authing:wx-analysis-flow-chitchat',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis-flow-chitchat',
            wechat_peer_name='分析流程闲聊微信',
            bound_at=utc_now(),
        )
    )
    storage.upsert_wechat_flow_session(
        'authing:wx-analysis-flow-chitchat',
        TaskType.PATENT_ANALYSIS.value,
        current_step='await_patent_input',
        draft_payload={},
        expires_at=utc_now() + timedelta(hours=12),
    )
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-analysis-flow-chitchat',
            owner_id='authing:wx-analysis-flow-chitchat',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='guided_workflow',
            active_context_session_id='flow-analysis-flow-chitchat',
            active_context_title='AI 分析',
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'chitchat',
            'confidence': 0.96,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis-flow-chitchat',
            text='你好',
        )
    )

    assert result.taskId is None
    assert '请发送专利号' in (result.messages[0].text or '')
    assert storage.list_tasks(owner_id='authing:wx-analysis-flow-chitchat') == []


def test_wechat_runtime_analysis_flow_patent_number_still_works_when_route_uncertain(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_analysis_flow_unknown_patent.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-analysis-flow-unknown-patent',
            authing_sub='wx-analysis-flow-unknown-patent',
            name='微信分析流程专利号用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-analysis-flow-unknown-patent',
            owner_id='authing:wx-analysis-flow-unknown-patent',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis-flow-unknown-patent',
            wechat_peer_name='分析流程专利号微信',
            bound_at=utc_now(),
        )
    )
    storage.upsert_wechat_flow_session(
        'authing:wx-analysis-flow-unknown-patent',
        TaskType.PATENT_ANALYSIS.value,
        current_step='await_patent_input',
        draft_payload={},
        expires_at=utc_now() + timedelta(hours=12),
    )
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-analysis-flow-unknown-patent',
            owner_id='authing:wx-analysis-flow-unknown-patent',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='guided_workflow',
            active_context_session_id='flow-analysis-flow-unknown-patent',
            active_context_title='AI 分析',
        )
    )
    monkeypatch.setattr(task_routes, '_enqueue_pipeline_task', lambda *args, **kwargs: None)
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'unknown',
            'confidence': 0.22,
            'requires_confirmation': True,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis-flow-unknown-patent',
            text='CN202410007777.7',
        )
    )

    assert result.taskId
    task = storage.get_task(result.taskId)
    assert task is not None
    assert task.task_type == TaskType.PATENT_ANALYSIS.value
    assert task.pn == 'CN202410007777.7'


@pytest.mark.parametrize('text', ['你好', '帮我看看', '这个怎么弄'])
def test_wechat_runtime_ambiguous_messages_return_guidance(monkeypatch, tmp_path, text):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_ambiguous.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-ambiguous',
            authing_sub='wx-ambiguous',
            name='微信模糊消息用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-ambiguous',
            owner_id='authing:wx-ambiguous',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-ambiguous',
            wechat_peer_name='模糊微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'unknown',
            'confidence': 0.31,
            'requires_confirmation': True,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-ambiguous',
            text=text,
        )
    )

    assert result.taskId is None
    assert result.sessionType is None
    assert (result.messages[0].text or '') == WECHAT_INTENT_GUIDANCE
    assert storage.list_tasks(owner_id='authing:wx-ambiguous') == []


def test_wechat_runtime_attachment_without_intent_returns_guidance(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_attachment_guidance.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-attachment',
            authing_sub='wx-attachment',
            name='微信附件用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-attachment',
            owner_id='authing:wx-attachment',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-attachment',
            wechat_peer_name='附件微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    uploaded = tmp_path / 'unknown.pdf'
    uploaded.write_bytes(b'pdf')

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-attachment',
            attachments=[InternalWeChatInboundAttachment(filename=uploaded.name, storedPath=str(uploaded), contentType='application/pdf')],
        )
    )

    assert result.taskId is None
    assert (result.messages[0].text or '') == WECHAT_INTENT_GUIDANCE
    assert storage.list_tasks(owner_id='authing:wx-attachment') == []


def test_wechat_runtime_explicit_search_creates_search_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_search.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search',
            authing_sub='wx-search',
            name='微信检索用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search',
            owner_id='authing:wx-search',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search',
            wechat_peer_name='检索微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.AI_SEARCH.value,
            'confidence': 0.95,
            'requires_confirmation': False,
            'extracted': {},
        },
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'create_session',
        lambda owner_id: SimpleNamespace(sessionId='search-session-1'),
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'stream_message',
        lambda *args, **kwargs: _empty_async_iter(),
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'get_snapshot',
        lambda *args, **kwargs: _build_ai_search_snapshot(session_id='search-session-1', title='固态电池检索'),
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search',
            text='帮我检索固态电池隔膜相关专利',
        )
    )

    assert result.taskId == 'search-session-1'
    assert result.sessionType == TaskType.AI_SEARCH.value
    assert '当前检索上下文' in (result.messages[0].text or '')


def test_wechat_runtime_llm_high_confidence_search_creates_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_search_llm.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-llm',
            authing_sub='wx-search-llm',
            name='微信检索 LLM 用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-llm',
            owner_id='authing:wx-search-llm',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-llm',
            wechat_peer_name='检索 LLM 微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.AI_SEARCH.value,
            'confidence': 0.94,
            'requires_confirmation': False,
            'extracted': {},
        },
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'create_session',
        lambda owner_id: SimpleNamespace(sessionId='search-session-llm'),
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'stream_message',
        lambda *args, **kwargs: _empty_async_iter(),
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'get_snapshot',
        lambda *args, **kwargs: _build_ai_search_snapshot(session_id='search-session-llm', title='LLM 检索'),
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-llm',
            text='查一下这个方向',
        )
    )

    assert result.taskId == 'search-session-llm'
    assert result.sessionType == TaskType.AI_SEARCH.value
    assert '当前检索上下文' in (result.messages[0].text or '')


def test_wechat_runtime_search_capability_question_does_not_create_search_task(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_search_capability_question.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-capability',
            authing_sub='wx-search-capability',
            name='微信检索能力用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-capability',
            owner_id='authing:wx-search-capability',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-capability',
            wechat_peer_name='检索能力微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'unknown',
            'confidence': 0.3,
            'requires_confirmation': True,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-capability',
            text='检索功能有哪些？',
        )
    )

    assert result.taskId is None
    assert result.sessionType is None
    assert (result.messages[0].text or '') == WECHAT_INTENT_GUIDANCE


@pytest.mark.parametrize('text', ['确认计划', '继续检索', '按当前结果完成', '选择 1 3'])
def test_wechat_runtime_ai_search_followup_commands_without_context_return_guidance(tmp_path, text):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_search_followup_blocked.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-followup',
            authing_sub='wx-search-followup',
            name='微信检索续接用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-followup',
            owner_id='authing:wx-search-followup',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-followup',
            wechat_peer_name='检索续接微信',
            bound_at=utc_now(),
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-followup',
            text=text,
        )
    )

    assert result.taskId is None
    assert result.sessionType is None
    assert (result.messages[0].text or '') == WECHAT_INTENT_GUIDANCE


def test_wechat_runtime_single_pending_search_reply_auto_resumes_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_single_pending_reply.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-pending-single',
            authing_sub='wx-search-pending-single',
            name='微信单检索待确认用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-pending-single',
            owner_id='authing:wx-search-pending-single',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-pending-single',
            wechat_peer_name='单检索待确认微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id='authing:wx-search-pending-single', task_type=TaskType.AI_SEARCH.value, title='隔膜检索')
    service = WeChatRuntimeService(task_manager=manager)
    state = {'answered': False}
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'unknown',
            'confidence': 0.21,
            'requires_confirmation': True,
            'extracted': {},
        },
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'list_sessions',
        lambda owner_id: SimpleNamespace(
            items=[
                SimpleNamespace(sessionId=task.id, title='隔膜检索', status='pending', phase='awaiting_user_answer', updatedAt='2026-01-03T00:00:00+00:00', createdAt='2026-01-01T00:00:00+00:00'),
            ]
        ),
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'get_snapshot',
        lambda session_id, owner_id: _build_ai_search_snapshot(
            session_id=session_id,
            title='隔膜检索',
            pending_action=None if state['answered'] else {
                'actionType': 'question',
                'questionId': 'q-1',
                'prompt': '请补充核心技术特征',
            },
        ),
    )

    async def _fake_answer(session_id: str, owner_id: str, question_id: str, answer: str):
        captured.update({'session_id': session_id, 'question_id': question_id, 'answer': answer})
        state['answered'] = True

    monkeypatch.setattr(service, 'answer_ai_search_question', _fake_answer)

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-pending-single',
            text='补充一个耐高温涂层特征',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert captured == {
        'session_id': task.id,
        'question_id': 'q-1',
        'answer': '补充一个耐高温涂层特征',
    }
    assert result.taskId == task.id
    assert result.sessionType == TaskType.AI_SEARCH.value
    assert conversation is not None
    assert conversation.active_context_kind == 'ai_search'
    assert conversation.active_context_session_id == task.id


def test_wechat_runtime_multiple_pending_search_replies_require_selection_then_replay(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_multi_pending_reply.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-pending-multi',
            authing_sub='wx-search-pending-multi',
            name='微信多检索待确认用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-pending-multi',
            owner_id='authing:wx-search-pending-multi',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-pending-multi',
            wechat_peer_name='多检索待确认微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    first = manager.create_task(owner_id='authing:wx-search-pending-multi', task_type=TaskType.AI_SEARCH.value, title='检索 A')
    second = manager.create_task(owner_id='authing:wx-search-pending-multi', task_type=TaskType.AI_SEARCH.value, title='检索 B')
    service = WeChatRuntimeService(task_manager=manager)
    answered_sessions: set[str] = set()
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'unknown',
            'confidence': 0.18,
            'requires_confirmation': True,
            'extracted': {},
        },
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'list_sessions',
        lambda owner_id: SimpleNamespace(
            items=[
                SimpleNamespace(sessionId=first.id, title='检索 A', status='pending', phase='awaiting_user_answer', updatedAt='2026-01-02T00:00:00+00:00', createdAt='2026-01-01T00:00:00+00:00'),
                SimpleNamespace(sessionId=second.id, title='检索 B', status='pending', phase='awaiting_user_answer', updatedAt='2026-01-03T00:00:00+00:00', createdAt='2026-01-02T00:00:00+00:00'),
            ]
        ),
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'get_snapshot',
        lambda session_id, owner_id: _build_ai_search_snapshot(
            session_id=session_id,
            title='检索 B' if session_id == second.id else '检索 A',
            pending_action=None if session_id in answered_sessions else {
                'actionType': 'question',
                'questionId': f'q-{session_id}',
                'prompt': '请补充核心技术特征',
            },
        ),
    )

    async def _fake_answer(session_id: str, owner_id: str, question_id: str, answer: str):
        captured.update({'session_id': session_id, 'question_id': question_id, 'answer': answer})
        answered_sessions.add(session_id)

    monkeypatch.setattr(service, 'answer_ai_search_question', _fake_answer)

    prompted = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-pending-multi',
            text='补充一个耐高温涂层特征',
        )
    )

    assert prompted.taskId is None
    assert '多个待确认中的检索' in (prompted.messages[0].text or '')
    assert '1. 检索 B' in (prompted.messages[0].text or '')

    selected = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-pending-multi',
            text='1',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert captured == {
        'session_id': second.id,
        'question_id': f'q-{second.id}',
        'answer': '补充一个耐高温涂层特征',
    }
    assert selected.taskId == second.id
    assert selected.sessionType == TaskType.AI_SEARCH.value
    assert conversation is not None
    assert conversation.active_context_kind == 'ai_search'
    assert conversation.active_context_session_id == second.id


def test_wechat_runtime_chitchat_does_not_continue_existing_ai_search_session(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_existing_search_blocked.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-existing-search',
            authing_sub='wx-existing-search',
            name='微信旧检索用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-existing-search',
            owner_id='authing:wx-existing-search',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-existing-search',
            wechat_peer_name='旧检索微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(
        owner_id='authing:wx-existing-search',
        task_type=TaskType.AI_SEARCH.value,
        title='AI 检索会话 - test',
    )
    storage.create_ai_search_message(
        {
            'message_id': 'msg-assistant-1',
            'task_id': task.id,
            'plan_version': None,
            'role': 'assistant',
            'kind': 'chat',
            'content': '已有旧检索会话',
            'stream_status': 'completed',
            'question_id': None,
            'metadata': {},
        }
    )
    service = WeChatRuntimeService(task_manager=manager)
    before_messages = storage.list_ai_search_messages(task.id)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'unknown',
            'confidence': 0.21,
            'requires_confirmation': True,
            'extracted': {},
        },
    )
    try:
        result = asyncio.run(
            service.handle_inbound_message(
                bot_account_id='bot-1',
                wechat_peer_id='wx-peer-existing-search',
                text='你好',
            )
        )
    finally:
        monkeypatch.undo()

    after_messages = storage.list_ai_search_messages(task.id)
    assert result.taskId is None
    assert result.sessionType is None
    assert (result.messages[0].text or '') == WECHAT_INTENT_GUIDANCE
    assert before_messages == after_messages


def test_wechat_runtime_exit_search_context_only_clears_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_exit_search.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-exit-search',
            authing_sub='wx-exit-search',
            name='微信退出检索用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-exit-search',
            owner_id='authing:wx-exit-search',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-exit-search',
            wechat_peer_name='退出检索微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(
        owner_id='authing:wx-exit-search',
        task_type=TaskType.AI_SEARCH.value,
        title='AI 检索会话 - exit',
    )
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-exit-search',
            owner_id='authing:wx-exit-search',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='ai_search',
            active_context_session_id=task.id,
            active_context_title='退出检索测试',
        )
    )
    service = WeChatRuntimeService(task_manager=manager)

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-exit-search',
            text='退出检索',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert result.taskId is None
    assert '退出当前检索上下文' in (result.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'none'
    assert storage.get_task(task.id) is not None


def test_wechat_runtime_search_context_cancel_intent_clears_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_search_context_cancel_intent.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-cancel-intent',
            authing_sub='wx-search-cancel-intent',
            name='微信检索退出意图用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-cancel-intent',
            owner_id='authing:wx-search-cancel-intent',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-cancel-intent',
            wechat_peer_name='检索退出意图微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(
        owner_id='authing:wx-search-cancel-intent',
        task_type=TaskType.AI_SEARCH.value,
        title='AI 检索会话 - cancel-intent',
    )
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-search-cancel-intent',
            owner_id='authing:wx-search-cancel-intent',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='ai_search',
            active_context_session_id=task.id,
            active_context_title='退出意图测试',
        )
    )
    service = WeChatRuntimeService(task_manager=manager)
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'cancel_or_pause',
            'confidence': 0.93,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-cancel-intent',
            text='先不检索了',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert result.taskId is None
    assert '退出当前检索上下文' in (result.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'none'
    assert storage.get_task(task.id) is not None


def test_wechat_runtime_cancel_missing_search_returns_structured_detail(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_cancel_missing_search.db')
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))

    with pytest.raises(HTTPException) as exc_info:
        service.cancel_current_search('authing:wx-missing-search', 'missing-search-session')

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail['code'] == 'WECHAT_SEARCH_SESSION_NOT_FOUND'
    assert '列出进行中的检索' in exc_info.value.detail['suggestion']


def test_wechat_runtime_multiple_search_sessions_require_selection(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_multi_search.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-multi-search',
            authing_sub='wx-multi-search',
            name='微信多检索用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-multi-search',
            owner_id='authing:wx-multi-search',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-multi-search',
            wechat_peer_name='多检索微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    first = manager.create_task(owner_id='authing:wx-multi-search', task_type=TaskType.AI_SEARCH.value, title='检索 A')
    second = manager.create_task(owner_id='authing:wx-multi-search', task_type=TaskType.AI_SEARCH.value, title='检索 B')
    service = WeChatRuntimeService(task_manager=manager)
    monkeypatch.setattr(
        service.ai_search_service,
        'list_sessions',
        lambda owner_id: SimpleNamespace(
            items=[
                SimpleNamespace(sessionId=first.id, title='检索 A', status='pending', phase='collecting_requirements', updatedAt='2026-01-02T00:00:00+00:00', createdAt='2026-01-01T00:00:00+00:00'),
                SimpleNamespace(sessionId=second.id, title='检索 B', status='pending', phase='collecting_requirements', updatedAt='2026-01-03T00:00:00+00:00', createdAt='2026-01-02T00:00:00+00:00'),
            ]
        ),
    )

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-multi-search',
            text='继续检索',
        )
    )

    assert result.taskId is None
    assert '多个未完成检索' in (result.messages[0].text or '')


def test_wechat_runtime_list_search_sessions_then_selects_context(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_list_search_sessions.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-list-search',
            authing_sub='wx-list-search',
            name='微信检索列表用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-list-search',
            owner_id='authing:wx-list-search',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search',
            wechat_peer_name='检索列表微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    first = manager.create_task(owner_id='authing:wx-list-search', task_type=TaskType.AI_SEARCH.value, title='固态电池隔膜检索')
    second = manager.create_task(owner_id='authing:wx-list-search', task_type=TaskType.AI_SEARCH.value, title='钠电正极检索')
    service = WeChatRuntimeService(task_manager=manager)

    monkeypatch.setattr(
        service.ai_search_service,
        'list_sessions',
        lambda owner_id: SimpleNamespace(
            items=[
                SimpleNamespace(sessionId=first.id, title='固态电池隔膜检索', status='pending', phase='collecting_requirements', updatedAt='2026-01-02T00:00:00+00:00', createdAt='2026-01-01T00:00:00+00:00'),
                SimpleNamespace(sessionId=second.id, title='钠电正极检索', status='pending', phase='collecting_requirements', updatedAt='2026-01-03T00:00:00+00:00', createdAt='2026-01-02T00:00:00+00:00'),
            ]
        ),
    )
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'list_search_sessions',
            'confidence': 0.98,
            'requires_confirmation': False,
            'extracted': {},
        },
    )
    monkeypatch.setattr(
        service.ai_search_service,
        'get_snapshot',
        lambda session_id, owner_id: _build_ai_search_snapshot(session_id=session_id, title='钠电正极检索' if session_id == second.id else '固态电池隔膜检索'),
    )

    listed = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search',
            text='有哪些正在进行的检索任务？',
        )
    )

    assert listed.taskId is None
    assert '进行中的检索' in (listed.messages[0].text or '')
    assert '1. 钠电正极检索' in (listed.messages[0].text or '')
    assert '2. 固态电池隔膜检索' in (listed.messages[0].text or '')

    selected = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search',
            text='1',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert selected.taskId == second.id
    assert selected.sessionType == TaskType.AI_SEARCH.value
    assert '当前检索上下文' in (selected.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'ai_search'
    assert conversation.active_context_session_id == second.id


@pytest.mark.parametrize(
    ('text', 'suffix'),
    [('退出选择', 'exit'), ('取消', 'cancel'), ('返回', 'back'), ('/cancel', 'slash_cancel')],
)
def test_wechat_runtime_list_search_sessions_escape_clears_pending(monkeypatch, tmp_path, text, suffix):
    storage = SQLiteTaskStorage(tmp_path / f'wechat_runtime_list_search_sessions_escape_{suffix}.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-list-search-escape',
            authing_sub='wx-list-search-escape',
            name='微信检索列表退出用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-list-search-escape',
            owner_id='authing:wx-list-search-escape',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search-escape',
            wechat_peer_name='检索列表退出微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    first = manager.create_task(owner_id='authing:wx-list-search-escape', task_type=TaskType.AI_SEARCH.value, title='固态电池隔膜检索')
    second = manager.create_task(owner_id='authing:wx-list-search-escape', task_type=TaskType.AI_SEARCH.value, title='钠电正极检索')
    service = WeChatRuntimeService(task_manager=manager)

    monkeypatch.setattr(
        service.ai_search_service,
        'list_sessions',
        lambda owner_id: SimpleNamespace(
            items=[
                SimpleNamespace(sessionId=first.id, title='固态电池隔膜检索', status='pending', phase='collecting_requirements', updatedAt='2026-01-02T00:00:00+00:00', createdAt='2026-01-01T00:00:00+00:00'),
                SimpleNamespace(sessionId=second.id, title='钠电正极检索', status='pending', phase='collecting_requirements', updatedAt='2026-01-03T00:00:00+00:00', createdAt='2026-01-02T00:00:00+00:00'),
            ]
        ),
    )
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'list_search_sessions',
            'confidence': 0.98,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    listed = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search-escape',
            text='有哪些正在进行的检索任务？',
        )
    )
    assert '进行中的检索' in (listed.messages[0].text or '')

    exited = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search-escape',
            text=text,
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert exited.taskId is None
    assert exited.sessionType is None
    assert '已退出检索选择' in (exited.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'none'
    assert not (conversation.memory or {}).get('pending_action')


def test_wechat_runtime_list_search_sessions_invalid_input_mentions_escape(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_list_search_sessions_invalid_input.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-list-search-invalid',
            authing_sub='wx-list-search-invalid',
            name='微信检索列表无效输入用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-list-search-invalid',
            owner_id='authing:wx-list-search-invalid',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search-invalid',
            wechat_peer_name='检索列表无效输入微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    first = manager.create_task(owner_id='authing:wx-list-search-invalid', task_type=TaskType.AI_SEARCH.value, title='固态电池隔膜检索')
    second = manager.create_task(owner_id='authing:wx-list-search-invalid', task_type=TaskType.AI_SEARCH.value, title='钠电正极检索')
    service = WeChatRuntimeService(task_manager=manager)

    monkeypatch.setattr(
        service.ai_search_service,
        'list_sessions',
        lambda owner_id: SimpleNamespace(
            items=[
                SimpleNamespace(sessionId=first.id, title='固态电池隔膜检索', status='pending', phase='collecting_requirements', updatedAt='2026-01-02T00:00:00+00:00', createdAt='2026-01-01T00:00:00+00:00'),
                SimpleNamespace(sessionId=second.id, title='钠电正极检索', status='pending', phase='collecting_requirements', updatedAt='2026-01-03T00:00:00+00:00', createdAt='2026-01-02T00:00:00+00:00'),
            ]
        ),
    )
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': 'list_search_sessions',
            'confidence': 0.98,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search-invalid',
            text='有哪些正在进行的检索任务？',
        )
    )

    invalid = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-list-search-invalid',
            text='你还能干什么？',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert '取消' in (invalid.messages[0].text or '')
    assert '返回' in (invalid.messages[0].text or '')
    assert conversation is not None
    assert (conversation.memory or {}).get('pending_action')


def test_wechat_runtime_switch_context_continue_current_keeps_search(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_switch_context_keep_search.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-switch-context-search',
            authing_sub='wx-switch-context-search',
            name='微信切换检索保留用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-switch-context-search',
            owner_id='authing:wx-switch-context-search',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-search',
            wechat_peer_name='切换检索保留微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id='authing:wx-switch-context-search', task_type=TaskType.AI_SEARCH.value, title='现有检索')
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-switch-context-search',
            owner_id='authing:wx-switch-context-search',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='ai_search',
            active_context_session_id=task.id,
            active_context_title='现有检索',
        )
    )
    service = WeChatRuntimeService(task_manager=manager)
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.PATENT_ANALYSIS.value,
            'confidence': 0.98,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    prompt = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-search',
            text='帮我分析这个专利',
        )
    )
    assert '回复“切换”可转到AI 分析' in (prompt.messages[0].text or '')

    kept = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-search',
            text='继续当前',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert '已保留当前检索' in (kept.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'ai_search'
    assert conversation.active_context_session_id == task.id
    assert not (conversation.memory or {}).get('pending_action')


@pytest.mark.parametrize(('text', 'suffix'), [('取消', 'cancel'), ('返回', 'back')])
def test_wechat_runtime_switch_context_escape_keeps_search(monkeypatch, tmp_path, text, suffix):
    storage = SQLiteTaskStorage(tmp_path / f'wechat_runtime_switch_context_escape_search_{suffix}.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-switch-context-search-escape',
            authing_sub='wx-switch-context-search-escape',
            name='微信切换检索退出用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-switch-context-search-escape',
            owner_id='authing:wx-switch-context-search-escape',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-search-escape',
            wechat_peer_name='切换检索退出微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id='authing:wx-switch-context-search-escape', task_type=TaskType.AI_SEARCH.value, title='现有检索')
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-switch-context-search-escape',
            owner_id='authing:wx-switch-context-search-escape',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='ai_search',
            active_context_session_id=task.id,
            active_context_title='现有检索',
        )
    )
    service = WeChatRuntimeService(task_manager=manager)
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.PATENT_ANALYSIS.value,
            'confidence': 0.98,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-search-escape',
            text='帮我分析这个专利',
        )
    )

    kept = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-search-escape',
            text=text,
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    assert '已保留当前检索' in (kept.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'ai_search'
    assert conversation.active_context_session_id == task.id
    assert not (conversation.memory or {}).get('pending_action')


def test_wechat_runtime_switch_context_escape_keeps_workflow(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_switch_context_escape_workflow.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-switch-context-workflow-escape',
            authing_sub='wx-switch-context-workflow-escape',
            name='微信切换流程退出用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-switch-context-workflow-escape',
            owner_id='authing:wx-switch-context-workflow-escape',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-workflow-escape',
            wechat_peer_name='切换流程退出微信',
            bound_at=utc_now(),
        )
    )
    storage.upsert_wechat_flow_session(
        'authing:wx-switch-context-workflow-escape',
        TaskType.PATENT_ANALYSIS.value,
        current_step='await_patent_input',
        draft_payload={},
        expires_at=utc_now() + timedelta(hours=12),
    )
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-switch-context-workflow-escape',
            owner_id='authing:wx-switch-context-workflow-escape',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='guided_workflow',
            active_context_session_id='flow-switch-context-workflow-escape',
            active_context_title='AI 分析',
        )
    )
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.AI_SEARCH.value,
            'confidence': 0.98,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    prompt = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-workflow-escape',
            text='我要检索固态电池隔膜',
        )
    )
    assert '回复“切换”可转到AI 检索' in (prompt.messages[0].text or '')

    kept = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-switch-context-workflow-escape',
            text='返回',
        )
    )

    conversation = storage.get_wechat_conversation_session(binding.binding_id)
    active_flow = storage.get_active_wechat_flow_session('authing:wx-switch-context-workflow-escape', TaskType.PATENT_ANALYSIS.value)
    assert '已保留当前流程' in (kept.messages[0].text or '')
    assert conversation is not None
    assert conversation.active_context_kind == 'guided_workflow'
    assert active_flow is not None
    assert not (conversation.memory or {}).get('pending_action')


def test_wechat_runtime_continue_search_without_human_decision_includes_next_step(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_continue_search_state_hint.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-search-next-step',
            authing_sub='wx-search-next-step',
            name='微信检索建议用户',
        )
    )
    binding = storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-search-next-step',
            owner_id='authing:wx-search-next-step',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-next-step',
            wechat_peer_name='检索建议微信',
            bound_at=utc_now(),
        )
    )
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id='authing:wx-search-next-step', task_type=TaskType.AI_SEARCH.value, title='隔膜检索')
    storage.upsert_wechat_conversation_session(
        WeChatConversationSession(
            conversation_id='wcs-search-next-step',
            owner_id='authing:wx-search-next-step',
            binding_id=binding.binding_id,
            status='active',
            active_context_kind='ai_search',
            active_context_session_id=task.id,
            active_context_title='隔膜检索',
        )
    )
    service = WeChatRuntimeService(task_manager=manager)
    monkeypatch.setattr(
        service.ai_search_service,
        'get_snapshot',
        lambda *args, **kwargs: _build_ai_search_snapshot(session_id=task.id, title='隔膜检索'),
    )

    async def _raise_continue(*args, **kwargs):
        raise HTTPException(
            status_code=409,
            detail={
                'code': 'HUMAN_DECISION_REQUIRED',
                'message': '现在还不用你来决定是否继续。',
                'suggestion': '想补充方向或条件的话，直接发给我就行。',
            },
        )

    monkeypatch.setattr(service, 'continue_ai_search', _raise_continue)

    result = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-search-next-step',
            text='继续检索',
        )
    )

    text = result.messages[0].text or ''
    assert '现在还不用你来决定是否继续' in text
    assert '直接发给我就行' in text


def test_wechat_runtime_reply_flow_collects_files_and_creates_task(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'wechat_runtime_reply.db')
    storage.upsert_authing_user(
        User(
            owner_id='authing:wx-reply',
            authing_sub='wx-reply',
            name='微信答复用户',
        )
    )
    storage.upsert_wechat_binding(
        WeChatBinding(
            binding_id='binding-reply',
            owner_id='authing:wx-reply',
            status='active',
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            wechat_peer_name='答复微信',
            bound_at=utc_now(),
        )
    )
    monkeypatch.setattr(task_routes, '_enqueue_pipeline_task', lambda *args, **kwargs: None)
    service = WeChatRuntimeService(task_manager=PipelineTaskManager(storage))
    monkeypatch.setattr(
        service.llm_service,
        'invoke_text_json',
        lambda *args, **kwargs: {
            'intent': TaskType.AI_REPLY.value,
            'confidence': 0.94,
            'requires_confirmation': False,
            'extracted': {},
        },
    )

    office_action = tmp_path / 'office_action.pdf'
    response_file = tmp_path / 'response.pdf'
    comparison_file = tmp_path / 'comparison.pdf'
    office_action.write_bytes(b'office-action')
    response_file.write_bytes(b'response')
    comparison_file.write_bytes(b'comparison')

    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            text='我要答复审查意见',
        )
    )
    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            attachments=[InternalWeChatInboundAttachment(filename=office_action.name, storedPath=str(office_action), contentType='application/pdf')],
        )
    )
    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            attachments=[InternalWeChatInboundAttachment(filename=response_file.name, storedPath=str(response_file), contentType='application/pdf')],
        )
    )
    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            text='跳过',
        )
    )
    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            text='跳过',
        )
    )
    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            attachments=[InternalWeChatInboundAttachment(filename=comparison_file.name, storedPath=str(comparison_file), contentType='application/pdf')],
        )
    )
    asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            text='完成对比文件',
        )
    )
    created = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-reply',
            text='开始答复',
        )
    )

    assert created.taskId
    task = storage.get_task(created.taskId)
    assert task is not None
    assert task.task_type == TaskType.AI_REPLY.value
    input_files = task.metadata.get('input_files') if isinstance(task.metadata, dict) else []
    assert [item['file_type'] for item in input_files] == ['office_action', 'response', 'comparison_doc']
