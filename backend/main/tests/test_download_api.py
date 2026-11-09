from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from main.instance_management import validate_instance_preset
from main.instance_tasks import create_instance_task_binding
from main.models import InstanceTask, Instances, Ports


User = get_user_model()
ATTENTION_STATUS = "Stan zadania wymaga ręcznej weryfikacji przez administratora."
SAFE_FAILURE_STATUS = "Nie udało się wykonać zadania."
SAFE_BUSY_MESSAGE = "Inna operacja serwera lub pobierania jest już w toku."


class DownloadApiTests(TransactionTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.test_root = Path(self.temp.name)
        self.media_root = self.test_root / "media"
        self.static_root = self.test_root / "static"
        self.arma_root = self.test_root / "arma3"
        self.mods_root = self.arma_root / "mods"
        for path in (self.media_root, self.static_root, self.arma_root, self.mods_root):
            path.mkdir(parents=True)

        self.settings_override = override_settings(
            MEDIA_ROOT=str(self.media_root),
            STATIC_ROOT=str(self.static_root),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.config_snapshot = {
            "paths": {
                "arma3": str(self.arma_root),
                "mods_directory": str(self.mods_root),
                "logs_directory": str(self.test_root / "operation-logs"),
                "download_directory": str(self.test_root / "downloads"),
                "steamcmd": str(self.test_root / "steamcmd"),
            },
            "steam_auth": {
                "username": "invented-steam-user",
                "password": "invented-steam-password",
                "shared_secret": "invented-shared-secret",
            },
        }
        self.snapshot_patcher = patch(
            "main.views.config.snapshot",
            return_value=self.config_snapshot,
        )
        self.snapshot_mock = self.snapshot_patcher.start()
        self.addCleanup(self.snapshot_patcher.stop)

        self.disk_usage_patcher = patch(
            "main.views.psutil.disk_usage",
            return_value=SimpleNamespace(free=1024**4),
        )
        self.disk_usage_mock = self.disk_usage_patcher.start()
        self.addCleanup(self.disk_usage_patcher.stop)

        self.owner = User.objects.create_user(
            username="download-owner",
            password="invented-password",
        )
        self.other = User.objects.create_user(
            username="download-other",
            password="invented-password",
        )
        self.staff = User.objects.create_user(
            username="download-staff",
            password="invented-password",
            is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            username="download-root",
            password="invented-password",
        )
        self.client = APIClient()
        self.next_instance_number = 0
        self.next_port_number = 24100

    def make_instance(
        self,
        owner=None,
        *,
        is_admin_instance=False,
        is_running=False,
        lifecycle_state=Instances.LifecycleState.STOPPED,
        pid=None,
    ):
        owner = owner or self.owner
        self.next_instance_number += 1
        self.next_port_number += 1
        name = f"download-api-{self.next_instance_number}"
        port = Ports.objects.create(
            port_number=self.next_port_number,
            is_available=False,
        )
        return Instances.objects.create(
            name=name,
            user=owner,
            preset=SimpleUploadedFile(
                f"{name}.html",
                b"<html><body>invented preset fixture</body></html>",
            ),
            port=port,
            is_admin_instance=is_admin_instance,
            is_running=is_running,
            lifecycle_state=lifecycle_state,
            pid=pid,
        )

    def create_binding(
        self,
        instance,
        *,
        kind=InstanceTask.Kind.DOWNLOAD,
        claimed=False,
        finished=False,
        superseded=False,
        attention_reason=None,
        workspace="",
    ):
        binding = create_instance_task_binding(instance, kind)
        update_fields = []
        if claimed:
            binding.claimed_at = timezone.now()
            update_fields.append("claimed_at")
        if finished:
            binding.finished_at = timezone.now()
            update_fields.append("finished_at")
        if superseded:
            binding.superseded_at = timezone.now()
            update_fields.append("superseded_at")
        if attention_reason is not None:
            binding.requires_attention = True
            binding.attention_reason = attention_reason
            binding.attention_workspace = workspace
            update_fields.extend(
                ("requires_attention", "attention_reason", "attention_workspace")
            )
        if update_fields:
            binding.save(update_fields=tuple(update_fields))
        return binding

    def post_download(self, actor, instance_id):
        self.client.force_authenticate(user=actor)
        return self.client.post(
            f"/api/instances/{instance_id}/download_mods/",
            {},
            format="json",
        )

    def get_task_status(self, actor, task_id):
        self.client.force_authenticate(user=actor)
        return self.client.get(f"/api/instances/task_status/{task_id}/")

    def test_download_commits_one_binding_dispatches_only_instance_id_and_reuses_id(self):
        instance = self.make_instance()

        def publish(*, kwargs, task_id):
            self.assertEqual(kwargs, {"instance_id": instance.pk})
            self.assertTrue(connection.get_autocommit())
            self.assertFalse(connection.in_atomic_block)
            binding = InstanceTask.objects.get(pk=task_id)
            self.assertEqual(binding.instance_id, instance.pk)
            self.assertEqual(binding.kind, InstanceTask.Kind.DOWNLOAD)
            self.assertIsNone(binding.claimed_at)
            self.assertIsNone(binding.finished_at)
            return SimpleNamespace(id="invented-broker-result-id")

        with patch("main.views.download_mods_task.apply_async", side_effect=publish) as dispatch:
            first = self.post_download(self.owner, instance.pk)
            second = self.post_download(self.owner, instance.pk)
            binding = InstanceTask.objects.get(instance=instance)
            binding.claimed_at = timezone.now()
            binding.requires_attention = True
            binding.attention_reason = InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED
            binding.attention_workspace = "C:/invented/private/same-instance-workspace"
            binding.save(
                update_fields=(
                    "claimed_at",
                    "requires_attention",
                    "attention_reason",
                    "attention_workspace",
                )
            )
            attention_duplicate = self.post_download(self.owner, instance.pk)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(attention_duplicate.status_code, 202)
        self.assertEqual(first.data["task_id"], second.data["task_id"])
        self.assertEqual(first.data["task_id"], attention_duplicate.data["task_id"])
        self.assertEqual(InstanceTask.objects.filter(instance=instance).count(), 1)
        self.assertEqual(InstanceTask.objects.get(instance=instance).task_id, first.data["task_id"])
        dispatch.assert_called_once_with(
            kwargs={"instance_id": instance.pk},
            task_id=first.data["task_id"],
        )

    def test_other_instance_queued_claimed_or_attention_download_returns_static_409(self):
        scenarios = (
            ("queued", {"claimed": False}),
            ("claimed", {"claimed": True}),
            (
                "attention",
                {
                    "claimed": True,
                    "attention_reason": InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                    "workspace": "C:/invented/private/attention-workspace",
                },
            ),
        )
        for label, binding_options in scenarios:
            with self.subTest(state=label):
                source = self.make_instance(owner=self.other)
                target = self.make_instance(owner=self.owner)
                existing_binding = self.create_binding(source, **binding_options)

                for actor in (self.owner, self.staff):
                    with self.subTest(actor=actor.username):
                        with patch(
                            "main.views.download_mods_task.apply_async",
                            return_value=SimpleNamespace(id="unexpected-queue-result"),
                        ) as dispatch:
                            response = self.post_download(actor, target.pk)

                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(response.data, {"message": SAFE_BUSY_MESSAGE})
                        dispatch.assert_not_called()
                        self.assertFalse(
                            InstanceTask.objects.filter(
                                instance=target,
                                finished_at__isnull=True,
                                superseded_at__isnull=True,
                            ).exists()
                        )
                InstanceTask.objects.filter(pk=existing_binding.pk).delete()

    def set_changed_download_preflight(self, scenario, instance):
        self.snapshot_mock.reset_mock()
        self.snapshot_mock.side_effect = None
        self.snapshot_mock.return_value = self.config_snapshot
        self.disk_usage_mock.reset_mock()
        self.disk_usage_mock.side_effect = None
        self.disk_usage_mock.return_value = SimpleNamespace(free=1024**4)

        if scenario == "config":
            self.snapshot_mock.side_effect = RuntimeError(
                "INVENTED_CHANGED_CONFIG_DETAIL"
            )
        elif scenario == "preset":
            Path(instance.preset.path).unlink()
        elif scenario == "disk":
            self.disk_usage_mock.return_value = SimpleNamespace(free=0)
        else:
            raise AssertionError(f"Unknown preflight scenario: {scenario}")

    def assert_download_preflight_was_skipped(self, preset_check):
        self.snapshot_mock.assert_not_called()
        preset_check.assert_not_called()
        self.disk_usage_mock.assert_not_called()

    def test_same_instance_reservation_precedes_changed_download_preflight(self):
        reservation_states = (
            ("queued", {"claimed": False}),
            ("claimed", {"claimed": True}),
            (
                "attention",
                {
                    "claimed": True,
                    "attention_reason": InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                    "workspace": "C:/invented/private/reused-attention-workspace",
                },
            ),
        )
        changed_preflight = ("config", "preset", "disk")

        for state, binding_options in reservation_states:
            for scenario in changed_preflight:
                with self.subTest(reservation=state, changed_preflight=scenario):
                    instance = self.make_instance()
                    binding = self.create_binding(instance, **binding_options)
                    self.set_changed_download_preflight(scenario, instance)

                    with (
                        patch(
                            "main.views.validate_instance_preset",
                            wraps=validate_instance_preset,
                        ) as preset_check,
                        patch(
                            "main.views.download_mods_task.apply_async",
                            side_effect=AssertionError(
                                "an existing download must not be republished"
                            ),
                        ) as dispatch,
                    ):
                        response = self.post_download(self.owner, instance.pk)

                    self.assertEqual(response.status_code, 202)
                    self.assertEqual(response.data, {"task_id": binding.task_id})
                    self.assert_download_preflight_was_skipped(preset_check)
                    dispatch.assert_not_called()
                    self.assertEqual(
                        InstanceTask.objects.filter(instance=instance).count(), 1
                    )
                    binding.refresh_from_db()
                    self.assertIsNone(binding.finished_at)
                    self.assertIsNone(binding.superseded_at)
                    self.assertEqual(binding.claimed_at is not None, binding_options["claimed"])
                    self.assertEqual(binding.requires_attention, state == "attention")

    def test_cross_instance_reservation_precedes_changed_download_preflight(self):
        reservation_states = (
            ("queued", {"claimed": False}),
            ("claimed", {"claimed": True}),
            (
                "attention",
                {
                    "claimed": True,
                    "attention_reason": InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                    "workspace": "C:/invented/private/busy-attention-workspace",
                },
            ),
        )
        changed_preflight = ("config", "preset", "disk")

        for state, binding_options in reservation_states:
            for scenario in changed_preflight:
                with self.subTest(reservation=state, changed_preflight=scenario):
                    source = self.make_instance(owner=self.other)
                    target = self.make_instance(owner=self.owner)
                    existing_binding = self.create_binding(source, **binding_options)
                    self.set_changed_download_preflight(scenario, target)

                    with (
                        patch(
                            "main.views.validate_instance_preset",
                            wraps=validate_instance_preset,
                        ) as preset_check,
                        patch(
                            "main.views.download_mods_task.apply_async",
                            side_effect=AssertionError(
                                "global busy must not publish a new download"
                            ),
                        ) as dispatch,
                    ):
                        response = self.post_download(self.owner, target.pk)

                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.data, {"message": SAFE_BUSY_MESSAGE})
                    self.assert_download_preflight_was_skipped(preset_check)
                    dispatch.assert_not_called()
                    self.assertFalse(InstanceTask.objects.filter(instance=target).exists())
                    self.assertTrue(
                        InstanceTask.objects.filter(pk=existing_binding.pk).exists()
                    )
                    InstanceTask.objects.filter(pk=existing_binding.pk).delete()

    def test_new_admission_preflight_failures_leave_no_binding_or_dispatch(self):
        expected_failures = {
            "config": (
                500,
                {"message": "Nie udało się zweryfikować pliku preset."},
            ),
            "preset": (
                400,
                {"message": "Plik preset instancji nie istnieje."},
            ),
            "disk": (
                400,
                {"message": "Brak miejsca na dysku. Skontaktuj sie z administratorem."},
            ),
        }

        for scenario, expected in expected_failures.items():
            with self.subTest(changed_preflight=scenario):
                instance = self.make_instance()
                self.set_changed_download_preflight(scenario, instance)

                with (
                    patch(
                        "main.views.validate_instance_preset",
                        wraps=validate_instance_preset,
                    ) as preset_check,
                    patch(
                        "main.views.download_mods_task.apply_async",
                        side_effect=AssertionError(
                            "failed preflight must not publish a download"
                        ),
                    ) as dispatch,
                ):
                    response = self.post_download(self.owner, instance.pk)

                self.assertEqual(response.status_code, expected[0])
                self.assertEqual(response.data, expected[1])
                self.assertNotIn("INVENTED_CHANGED_CONFIG_DETAIL", repr(response.data))
                dispatch.assert_not_called()
                self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())
                if scenario == "config":
                    self.snapshot_mock.assert_called_once_with()
                    preset_check.assert_not_called()
                    self.disk_usage_mock.assert_not_called()
                elif scenario == "preset":
                    self.snapshot_mock.assert_called_once_with()
                    preset_check.assert_called_once()
                    self.disk_usage_mock.assert_not_called()
                else:
                    self.snapshot_mock.assert_called_once_with()
                    preset_check.assert_called_once()
                    self.disk_usage_mock.assert_called_once_with("/")

    def test_admin_superuser_check_runs_only_for_new_download_admission(self):
        admin_duplicate = self.make_instance(
            owner=self.superuser,
            is_admin_instance=True,
        )
        duplicate_binding = self.create_binding(admin_duplicate)
        with (
            patch("main.views.get_super_user", return_value=None) as admin_lookup,
            patch(
                "main.views.download_mods_task.apply_async",
                side_effect=AssertionError("an existing admin download is reused"),
            ) as dispatch,
        ):
            duplicate = self.post_download(self.staff, admin_duplicate.pk)

        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(duplicate.data, {"task_id": duplicate_binding.task_id})
        admin_lookup.assert_not_called()
        dispatch.assert_not_called()
        InstanceTask.objects.filter(pk=duplicate_binding.pk).delete()

        source = self.make_instance(owner=self.other)
        source_binding = self.create_binding(source)
        admin_target = self.make_instance(
            owner=self.superuser,
            is_admin_instance=True,
        )
        with (
            patch("main.views.get_super_user", return_value=None) as admin_lookup,
            patch(
                "main.views.download_mods_task.apply_async",
                side_effect=AssertionError("global busy must not publish"),
            ) as dispatch,
        ):
            busy = self.post_download(self.staff, admin_target.pk)

        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.data, {"message": SAFE_BUSY_MESSAGE})
        admin_lookup.assert_not_called()
        dispatch.assert_not_called()
        self.assertFalse(InstanceTask.objects.filter(instance=admin_target).exists())
        self.assertTrue(InstanceTask.objects.filter(pk=source_binding.pk).exists())
        InstanceTask.objects.filter(pk=source_binding.pk).delete()

        new_admin_target = self.make_instance(
            owner=self.superuser,
            is_admin_instance=True,
        )
        with (
            patch("main.views.get_super_user", return_value=None) as admin_lookup,
            patch(
                "main.views.download_mods_task.apply_async",
                side_effect=AssertionError("missing admin must prevent publishing"),
            ) as dispatch,
        ):
            rejected = self.post_download(self.staff, new_admin_target.pk)

        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(rejected.data, {"message": "Nie znaleziono administratora."})
        admin_lookup.assert_called_once_with()
        dispatch.assert_not_called()
        self.assertFalse(InstanceTask.objects.filter(instance=new_admin_target).exists())

    def test_nonowner_nonstaff_is_rejected_before_download_backend_access(self):
        target = self.make_instance(owner=self.other)
        self.snapshot_mock.reset_mock()
        self.disk_usage_mock.reset_mock()

        with patch(
            "main.views.download_mods_task.apply_async",
            return_value=SimpleNamespace(id="unexpected-queue-result"),
        ) as dispatch:
            response = self.post_download(self.owner, target.pk)

        self.assertEqual(response.status_code, 404)
        self.snapshot_mock.assert_not_called()
        self.disk_usage_mock.assert_not_called()
        dispatch.assert_not_called()
        self.assertFalse(InstanceTask.objects.filter(instance=target).exists())

    def test_running_instance_download_uses_not_stopped_admission_conflict(self):
        instance = self.make_instance(
            is_running=True,
            lifecycle_state=Instances.LifecycleState.RUNNING,
            pid=43210,
        )

        with patch(
            "main.views.download_mods_task.apply_async",
            return_value=SimpleNamespace(id="unexpected-queue-result"),
        ) as dispatch:
            response = self.post_download(self.owner, instance.pk)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.data,
            {"message": "Serwer musi być zatrzymany przed pobraniem modów."},
        )
        dispatch.assert_not_called()
        self.assertFalse(InstanceTask.objects.filter(instance=instance).exists())

    def test_unclaimed_publish_failure_removes_only_its_new_binding(self):
        instance = self.make_instance()
        unrelated_instance = self.make_instance(owner=self.other)
        unrelated = self.create_binding(
            unrelated_instance,
            kind=InstanceTask.Kind.START,
            finished=True,
        )
        attempted_task_ids = []
        private_queue_error = "PRIVATE_QUEUE_TRANSPORT_DETAIL"

        def fail_publish(*, kwargs, task_id):
            self.assertEqual(kwargs, {"instance_id": instance.pk})
            attempted_task_ids.append(task_id)
            raise RuntimeError(private_queue_error)

        with patch(
            "main.views.download_mods_task.apply_async",
            side_effect=fail_publish,
        ) as dispatch:
            response = self.post_download(self.owner, instance.pk)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.data,
            {"message": "Nie udało się rozpocząć zadania pobierania."},
        )
        self.assertNotIn(private_queue_error, repr(response.data))
        dispatch.assert_called_once()
        self.assertEqual(len(attempted_task_ids), 1)
        self.assertFalse(InstanceTask.objects.filter(pk=attempted_task_ids[0]).exists())
        self.assertTrue(InstanceTask.objects.filter(pk=unrelated.pk).exists())
        unrelated.refresh_from_db()
        self.assertIsNotNone(unrelated.finished_at)

    def test_claimed_publish_failure_keeps_ambiguous_binding_and_duplicate_does_not_requeue(self):
        instance = self.make_instance()
        attempted_task_ids = []
        private_queue_error = "PRIVATE_AMBIGUOUS_ACK_DETAIL"

        def claim_then_fail(*, kwargs, task_id):
            self.assertEqual(kwargs, {"instance_id": instance.pk})
            attempted_task_ids.append(task_id)
            InstanceTask.objects.filter(pk=task_id).update(claimed_at=timezone.now())
            raise RuntimeError(private_queue_error)

        with patch(
            "main.views.download_mods_task.apply_async",
            side_effect=claim_then_fail,
        ) as dispatch:
            first = self.post_download(self.owner, instance.pk)
            second = self.post_download(self.owner, instance.pk)

        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.data["task_id"], attempted_task_ids[0])
        self.assertNotIn(private_queue_error, repr(first.data))
        dispatch.assert_called_once()
        binding = InstanceTask.objects.get(pk=attempted_task_ids[0])
        self.assertIsNotNone(binding.claimed_at)
        self.assertIsNone(binding.finished_at)
        self.assertIsNone(binding.superseded_at)

    def test_attention_status_is_static_and_checked_before_async_result(self):
        instance = self.make_instance()
        private_workspace = "C:/invented/private/attention-workspace-71"
        private_reason = InstanceTask.AttentionReason.PROCESS_CLEANUP_UNCONFIRMED
        binding = self.create_binding(
            instance,
            claimed=True,
            attention_reason=private_reason,
            workspace=private_workspace,
        )

        with patch(
            "main.views.AsyncResult",
            side_effect=AssertionError("attention status must not query AsyncResult"),
        ) as async_result:
            response = self.get_task_status(self.owner, binding.task_id)
            foreign_response = self.get_task_status(self.other, binding.task_id)

        expected = {
            "id": binding.task_id,
            "state": "REQUIRES_ATTENTION",
            "result": {"status": ATTENTION_STATUS},
        }
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected)
        self.assertEqual(foreign_response.status_code, 404)
        async_result.assert_not_called()
        rendered = repr(response.data)
        for private_value in (
            "requires_attention",
            "attention_reason",
            "attention_workspace",
            private_reason,
            private_workspace,
        ):
            self.assertNotIn(private_value, rendered)

    def test_unknown_and_foreign_task_statuses_never_query_async_result(self):
        instance = self.make_instance()
        binding = self.create_binding(instance)

        def read_staff_result(task_id):
            if task_id != binding.task_id:
                raise AssertionError("unauthorized status must not query AsyncResult")
            return SimpleNamespace(status="PENDING", result=None)

        with patch(
            "main.views.AsyncResult",
            side_effect=read_staff_result,
        ) as async_result:
            foreign = self.get_task_status(self.other, binding.task_id)
            missing = self.get_task_status(self.owner, "invented-unknown-task")
            async_result.assert_not_called()
            staff_response = self.get_task_status(self.staff, binding.task_id)

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(staff_response.status_code, 200)
        self.assertEqual(staff_response.data["state"], "PENDING")
        async_result.assert_called_once_with(binding.task_id)

    def test_superseded_task_remains_visible_only_to_an_authorized_actor(self):
        instance = self.make_instance()
        binding = self.create_binding(
            instance,
            kind=InstanceTask.Kind.STOP,
            superseded=True,
        )

        with patch(
            "main.views.AsyncResult",
            side_effect=AssertionError("superseded status must not query AsyncResult"),
        ) as async_result:
            owner_response = self.get_task_status(self.owner, binding.task_id)
            foreign_response = self.get_task_status(self.other, binding.task_id)

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response.data["id"], binding.task_id)
        self.assertEqual(owner_response.data["state"], "REVOKED")
        self.assertEqual(
            owner_response.data["result"],
            {"status": "Zadanie zostało zastąpione przez nowszą próbę zatrzymania."},
        )
        self.assertEqual(foreign_response.status_code, 404)
        async_result.assert_not_called()

    def test_unclaimed_pending_download_remains_queued(self):
        instance = self.make_instance()
        binding = self.create_binding(instance)
        pending_result = SimpleNamespace(status="PENDING", result=None)

        with patch("main.views.AsyncResult", return_value=pending_result) as async_result:
            response = self.get_task_status(self.owner, binding.task_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {"id": binding.task_id, "state": "PENDING", "result": None},
        )
        async_result.assert_called_once_with(binding.task_id)
        binding.refresh_from_db()
        self.assertIsNone(binding.claimed_at)
        self.assertIsNone(binding.finished_at)
        self.assertFalse(binding.requires_attention)

    def test_claimed_pending_download_remains_active_without_requeue_or_finish(self):
        instance = self.make_instance()
        binding = self.create_binding(instance, claimed=True)
        pending_result = SimpleNamespace(status="PENDING", result=None)

        with (
            patch("main.views.AsyncResult", return_value=pending_result) as async_result,
            patch(
                "main.views.download_mods_task.apply_async",
                return_value=SimpleNamespace(id="unexpected-queue-result"),
            ) as dispatch,
        ):
            response = self.get_task_status(self.owner, binding.task_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "PENDING")
        self.assertEqual(response.data["result"], None)
        async_result.assert_called_once_with(binding.task_id)
        dispatch.assert_not_called()
        binding.refresh_from_db()
        self.assertIsNotNone(binding.claimed_at)
        self.assertIsNone(binding.finished_at)
        self.assertFalse(binding.requires_attention)

    def test_claimed_terminal_backend_state_becomes_durable_safe_attention(self):
        backend_states = ("SUCCESS", "FAILURE", "REVOKED")
        private_error = "PRIVATE_BACKEND_EXCEPTION_AND_ARGUMENTS"

        class SensitiveBackendError(Exception):
            def __str__(self):
                return "PRIVATE_BACKEND_EXCEPTION_TEXT"

        for state in backend_states:
            with self.subTest(backend_state=state):
                instance = self.make_instance()
                binding = self.create_binding(instance, claimed=True)
                raw_result = SensitiveBackendError(private_error)
                raw_result.__cause__ = RuntimeError("PRIVATE_BACKEND_CAUSE")
                backend_result = SimpleNamespace(status=state, result=raw_result)

                with patch(
                    "main.views.AsyncResult",
                    return_value=backend_result,
                ) as async_result:
                    response = self.get_task_status(self.owner, binding.task_id)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["state"], "REQUIRES_ATTENTION")
                self.assertEqual(response.data["result"], {"status": ATTENTION_STATUS})
                async_result.assert_called_once_with(binding.task_id)
                rendered = repr(response.data)
                for private_value in (
                    private_error,
                    "PRIVATE_BACKEND_EXCEPTION_TEXT",
                    "PRIVATE_BACKEND_CAUSE",
                ):
                    self.assertNotIn(private_value, rendered)
                binding.refresh_from_db()
                self.assertIsNone(binding.finished_at)
                self.assertTrue(binding.requires_attention)
                self.assertEqual(
                    binding.attention_reason,
                    InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                )

    def test_unclaimed_terminal_backend_state_becomes_attention_and_keeps_global_fence(self):
        backend_states = ("SUCCESS", "FAILURE", "REVOKED")
        private_result = {
            "status": "PRIVATE_UNCLAIMED_TERMINAL_RESULT",
            "details": "PRIVATE_UNCLAIMED_TERMINAL_DETAILS",
        }

        for state in backend_states:
            with self.subTest(backend_state=state):
                instance = self.make_instance()
                binding = self.create_binding(instance)
                backend_result = SimpleNamespace(status=state, result=private_result)

                with patch(
                    "main.views.AsyncResult",
                    return_value=backend_result,
                ) as async_result:
                    response = self.get_task_status(self.owner, binding.task_id)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.data,
                    {
                        "id": binding.task_id,
                        "state": "REQUIRES_ATTENTION",
                        "result": {"status": ATTENTION_STATUS},
                    },
                )
                async_result.assert_called_once_with(binding.task_id)
                rendered = repr(response.data)
                self.assertNotIn("PRIVATE_UNCLAIMED_TERMINAL_RESULT", rendered)
                self.assertNotIn("PRIVATE_UNCLAIMED_TERMINAL_DETAILS", rendered)
                binding.refresh_from_db()
                self.assertIsNone(binding.claimed_at)
                self.assertIsNone(binding.finished_at)
                self.assertIsNone(binding.superseded_at)
                self.assertTrue(binding.requires_attention)
                self.assertEqual(
                    binding.attention_reason,
                    InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
                )

                target = self.make_instance(owner=self.other)
                with patch(
                    "main.views.download_mods_task.apply_async",
                    side_effect=AssertionError(
                        "an attention reservation must retain the global fence"
                    ),
                ) as dispatch:
                    busy = self.post_download(self.staff, target.pk)

                self.assertEqual(busy.status_code, 409)
                self.assertEqual(busy.data, {"message": SAFE_BUSY_MESSAGE})
                dispatch.assert_not_called()
                self.assertFalse(InstanceTask.objects.filter(instance=target).exists())

    def test_fresh_binding_read_preserves_completion_racing_backend_terminal_state(self):
        instance = self.make_instance()
        binding = self.create_binding(instance, claimed=True)
        terminal_result = {"status": "completed", "installed": 3}

        def finish_before_backend_reply(task_id):
            InstanceTask.objects.filter(
                pk=task_id,
                finished_at__isnull=True,
            ).update(finished_at=timezone.now())
            return SimpleNamespace(status="SUCCESS", result=terminal_result)

        with patch("main.views.AsyncResult", side_effect=finish_before_backend_reply) as async_result:
            response = self.get_task_status(self.owner, binding.task_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["state"], "SUCCESS")
        self.assertEqual(response.data["result"], terminal_result)
        async_result.assert_called_once_with(binding.task_id)
        binding.refresh_from_db()
        self.assertIsNotNone(binding.finished_at)
        self.assertFalse(binding.requires_attention)
        self.assertEqual(binding.attention_reason, "")

    def test_fresh_supersession_racing_unclaimed_terminal_state_wins_over_attention(self):
        instance = self.make_instance()
        binding = self.create_binding(instance)
        private_result = {"status": "PRIVATE_STALE_TERMINAL_RESULT"}

        def supersede_before_backend_reply(task_id):
            InstanceTask.objects.filter(
                pk=task_id,
                superseded_at__isnull=True,
            ).update(superseded_at=timezone.now())
            return SimpleNamespace(status="REVOKED", result=private_result)

        with patch(
            "main.views.AsyncResult",
            side_effect=supersede_before_backend_reply,
        ) as async_result:
            response = self.get_task_status(self.owner, binding.task_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "id": binding.task_id,
                "state": "REVOKED",
                "result": {
                    "status": "Zadanie zostało zastąpione przez nowszą próbę zatrzymania."
                },
            },
        )
        async_result.assert_called_once_with(binding.task_id)
        self.assertNotIn("PRIVATE_STALE_TERMINAL_RESULT", repr(response.data))
        binding.refresh_from_db()
        self.assertIsNotNone(binding.superseded_at)
        self.assertFalse(binding.requires_attention)
        self.assertIsNone(binding.finished_at)

    def test_finished_binding_with_expired_pending_result_is_retired_and_returns_404(self):
        instance = self.make_instance()
        binding = self.create_binding(instance, finished=True)
        pending_result = SimpleNamespace(status="PENDING", result=None)

        with patch("main.views.AsyncResult", return_value=pending_result) as async_result:
            response = self.get_task_status(self.owner, binding.task_id)

        self.assertEqual(response.status_code, 404)
        async_result.assert_called_once_with(binding.task_id)
        self.assertFalse(InstanceTask.objects.filter(pk=binding.task_id).exists())

    def test_normal_terminal_results_keep_success_shape_and_hide_all_failure_details(self):
        success_instance = self.make_instance()
        success_binding = self.create_binding(success_instance, finished=True)
        success_result = {"status": "completed", "installed": 2}
        with patch(
            "main.views.AsyncResult",
            return_value=SimpleNamespace(status="SUCCESS", result=success_result),
        ) as async_result:
            success_response = self.get_task_status(self.owner, success_binding.task_id)

        self.assertEqual(success_response.status_code, 200)
        self.assertEqual(success_response.data["state"], "SUCCESS")
        self.assertEqual(success_response.data["result"], success_result)
        async_result.assert_called_once_with(success_binding.task_id)

        failure_instance = self.make_instance()
        failure_binding = self.create_binding(failure_instance, finished=True)

        class SensitiveFailure(Exception):
            def __str__(self):
                return "PRIVATE_FAILURE_STRING"

        failure = SensitiveFailure("PRIVATE_FAILURE_ARGUMENT")
        failure.__cause__ = RuntimeError("PRIVATE_FAILURE_CAUSE")
        with patch(
            "main.views.AsyncResult",
            return_value=SimpleNamespace(status="FAILURE", result=failure),
        ) as async_result:
            failure_response = self.get_task_status(self.owner, failure_binding.task_id)

        self.assertEqual(failure_response.status_code, 200)
        self.assertEqual(failure_response.data["state"], "FAILURE")
        self.assertEqual(
            failure_response.data["result"],
            {"status": SAFE_FAILURE_STATUS},
        )
        async_result.assert_called_once_with(failure_binding.task_id)
        rendered = repr(failure_response.data)
        for private_value in (
            "PRIVATE_FAILURE_STRING",
            "PRIVATE_FAILURE_ARGUMENT",
            "PRIVATE_FAILURE_CAUSE",
        ):
            self.assertNotIn(private_value, rendered)

    def test_attention_binding_blocks_start_before_start_backend_or_dispatch(self):
        instance = self.make_instance()
        self.create_binding(
            instance,
            claimed=True,
            attention_reason=InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
            workspace="C:/invented/private/start-attention-workspace",
        )
        self.snapshot_mock.reset_mock()
        self.client.force_authenticate(user=self.owner)

        with patch(
            "main.views.dispatch_instance_task",
            side_effect=AssertionError("attention must block start dispatch"),
        ) as dispatch:
            response = self.client.post(
                f"/api/instances/{instance.pk}/start/",
                {},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.snapshot_mock.assert_not_called()
        dispatch.assert_not_called()
        instance.refresh_from_db()
        self.assertEqual(instance.lifecycle_state, Instances.LifecycleState.STOPPED)
        self.assertFalse(
            InstanceTask.objects.filter(
                instance=instance,
                kind=InstanceTask.Kind.START,
            ).exists()
        )

    def test_attention_binding_blocks_delete_without_releasing_instance_or_port(self):
        instance = self.make_instance()
        self.create_binding(
            instance,
            claimed=True,
            attention_reason=InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
            workspace="C:/invented/private/delete-attention-workspace",
        )
        preset_path = Path(instance.preset.path)
        self.client.force_authenticate(user=self.owner)

        response = self.client.delete(f"/api/instances/{instance.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Instances.objects.filter(pk=instance.pk).exists())
        self.assertTrue(preset_path.exists())
        instance.port.refresh_from_db()
        self.assertFalse(instance.port.is_available)

    def test_attention_binding_blocks_admin_preset_replacement(self):
        instance = self.make_instance(
            owner=self.superuser,
            is_admin_instance=True,
        )
        self.create_binding(
            instance,
            claimed=True,
            attention_reason=InstanceTask.AttentionReason.WORKER_STATE_UNVERIFIED,
            workspace="C:/invented/private/preset-attention-workspace",
        )
        original_preset = instance.preset.name
        original_path = Path(instance.preset.path)
        self.client.force_authenticate(user=self.superuser)
        log_context = MagicMock()
        log_context.__enter__.return_value = {"change_preset": Mock()}
        log_context.__exit__.return_value = False

        with patch("main.views.operation_loggers", return_value=log_context):
            response = self.client.post(
                f"/api/instances/{instance.pk}/admin_instance/change_preset/",
                {
                    "preset": SimpleUploadedFile(
                        "replacement.html",
                        b"<html><body>replacement fixture</body></html>",
                    )
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 400)
        instance.refresh_from_db()
        self.assertEqual(instance.preset.name, original_preset)
        self.assertTrue(original_path.exists())
        self.assertFalse(list(self.media_root.rglob("replacement.html")))
