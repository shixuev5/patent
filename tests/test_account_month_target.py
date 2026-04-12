from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.models import (
    AccountMonthTargetUpsertRequest,
    AccountNotificationSettingsUpdateRequest,
    AccountProfileUpdateRequest,
    AccountWeChatIntegrationUpdateRequest,
    InternalWeChatBindCodeCompleteRequest,
    InternalWeChatBindSessionCompleteRequest,
    InternalWeChatDeliveryJobClaimRequest,
    InternalWeChatDeliveryJobResolveRequest,
    InternalWeChatInboundAttachment,
    CurrentUser,
)
from backend.notifications.task_wechat_service import TaskWeChatNotificationService
from backend.routes import account
from backend.routes import tasks as task_routes
from backend.storage import Task, TaskStatus, TaskType, User, WeChatBinding
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage
from backend.time_utils import utc_now
from backend.wechat_runtime import WeChatRuntimeService
from config import settings
from fastapi import HTTPException


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

    bind_session = asyncio.run(account.post_account_wechat_bind_session(current_user=user))
    assert bind_session.status == 'pending'
    assert bind_session.bindCode
    assert bind_session.qrSvg.startswith('<svg')

    completed = asyncio.run(
        account.post_internal_wechat_bind_session_complete(
            bind_session_id=bind_session.bindSessionId,
            payload=InternalWeChatBindSessionCompleteRequest(
                botAccountId='bot-1',
                wechatPeerId='wx-peer-001',
                wechatPeerName='测试微信',
            ),
            _token='internal-test-token',
        )
    )
    assert completed['binding']['wechatPeerName'] == '测试微信'

    integrated = asyncio.run(account.get_account_wechat_integration(current_user=user))
    assert integrated.bindingStatus == 'bound'
    assert integrated.binding is not None
    assert integrated.binding.wechatPeerIdMasked is not None
    assert integrated.bindSession is not None
    assert integrated.bindSession.status == 'bound'

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


def test_account_wechat_bind_session_complete_by_code(monkeypatch, tmp_path):
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

    bind_session = asyncio.run(account.post_account_wechat_bind_session(current_user=user))
    completed = asyncio.run(
        account.post_internal_wechat_bind_session_complete_by_code(
            payload=InternalWeChatBindCodeCompleteRequest(
                bindCode=bind_session.bindCode,
                botAccountId='bot-1',
                wechatPeerId='wx-peer-by-code',
                wechatPeerName='扫码微信',
            ),
            _token='internal-test-token',
        )
    )
    assert completed['binding']['wechatPeerName'] == '扫码微信'
    integrated = asyncio.run(account.get_account_wechat_integration(current_user=user))
    assert integrated.bindingStatus == 'bound'


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

    started = asyncio.run(
        service.handle_inbound_message(
            bot_account_id='bot-1',
            wechat_peer_id='wx-peer-analysis',
            text='/analysis new',
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
    assert '不能确定你的意图' in (result.messages[0].text or '')


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
            text='/reply new',
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
