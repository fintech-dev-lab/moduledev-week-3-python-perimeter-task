from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "public_check.py"
SPEC = importlib.util.spec_from_file_location("week3_public_check", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load public_check.py")
public_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = public_check
SPEC.loader.exec_module(public_check)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PACKAGE = Path(__file__).resolve().parents[2]


class FixtureTests(unittest.TestCase):
    def test_published_fixture_digest_is_valid(self) -> None:
        fixture = public_check.load_fixture(FIXTURES)
        self.assertEqual(
            fixture["digest"], public_check.canonical_fixture_digest(FIXTURES)
        )
        self.assertEqual(fixture["provider"]["image"], public_check.PROVIDER_IMAGE)
        auto = json.loads(
            (FIXTURES / fixture["files"]["reviewAutoRequest"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(auto["amount"], fixture["limit"]["amount"])

    def test_fixture_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "fixtures"
            shutil.copytree(FIXTURES, copied)
            payload = copied / "data" / "request-provider.json"
            payload.write_text('{"changed":true}\n', encoding="utf-8")
            with self.assertRaises(public_check.FixtureError):
                public_check.load_fixture(copied)

    def test_fixture_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "fixtures"
            shutil.copytree(FIXTURES, copied)
            metadata_path = copied / "fixture.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["files"]["providerRequest"] = "../outside.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaises(public_check.FixtureError):
                public_check.canonical_fixture_digest(copied)


class ReceiptContractTests(unittest.TestCase):
    def test_legacy_mapping_and_exact_bytes(self) -> None:
        legacy = {
            "providerPaymentId": "provider-123",
            "operationId": "external-123",
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-09-04T12:00:00.123Z",
        }
        receipt = public_check.normalized_receipt(legacy)
        self.assertEqual(receipt["messageId"], "provider-123")
        self.assertEqual(receipt["externalRequestId"], "external-123")
        self.assertEqual(receipt["occurredAt"], legacy["occurredAt"])
        self.assertNotIn("message", receipt)
        self.assertEqual(
            public_check.receipt_bytes(receipt),
            b'{"externalRequestId":"external-123","messageId":"provider-123",'
            b'"occurredAt":"2026-09-04T12:00:00.123Z","outcome":"COMPLETED",'
            b'"providerPaymentId":"provider-123","version":1}',
        )

    def test_hmac_uses_raw_utf8_secret_and_exact_body(self) -> None:
        body = b'{"version":1}'
        expected = "v1=" + hashlib.sha256(b"not-the-hmac").hexdigest()
        actual = public_check.receipt_signature(" raw-secret ", body)
        self.assertTrue(actual.startswith("v1="))
        self.assertNotEqual(actual, expected)
        self.assertNotEqual(
            actual,
            public_check.receipt_signature("raw-secret", body),
        )
        self.assertNotEqual(
            actual,
            public_check.receipt_signature(" raw-secret ", body + b"\n"),
        )

    def test_unknown_fields_and_crlf_are_rejected(self) -> None:
        base = {
            "providerPaymentId": "provider-123",
            "operationId": "external-123",
            "result": "REJECTED",
            "message": "Payment rejected",
            "occurredAt": "2026-09-04T12:00:00Z",
        }
        with self.assertRaises(ValueError):
            public_check.normalized_receipt({**base, "unknown": True})
        for field in ("providerPaymentId", "operationId", "occurredAt", "message"):
            for value in ("\rprefix", "middle\nvalue", "suffix\r"):
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaises(ValueError):
                        public_check.normalized_receipt({**base, field: value})

    def test_duplicate_and_conflict_keep_one_message_identity(self) -> None:
        base = {
            "providerPaymentId": "provider-123",
            "operationId": "external-123",
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-09-04T12:00:00Z",
        }
        duplicate = dict(base)
        conflict = {**base, "result": "REJECTED", "message": "Late rejection"}
        first = public_check.normalized_receipt(base)
        second = public_check.normalized_receipt(duplicate)
        changed = public_check.normalized_receipt(conflict)
        self.assertEqual(first["messageId"], second["messageId"])
        self.assertEqual(first["messageId"], changed["messageId"])
        self.assertEqual(
            public_check.receipt_bytes(first), public_check.receipt_bytes(second)
        )
        self.assertNotEqual(
            public_check.receipt_bytes(first), public_check.receipt_bytes(changed)
        )

    def test_external_text_id_is_validated_and_sql_encoded(self) -> None:
        value = "external'; SELECT sensitive"
        self.assertEqual(public_check.PublicChecker._text_id(value, "id"), value)
        expression = public_check.PublicChecker._sql_text(value)
        self.assertNotIn(value, expression)
        self.assertNotIn(";", expression)
        with self.assertRaises(public_check.ContractError):
            public_check.PublicChecker._text_id("external\r\nheader", "id")


class ComposeAdmissionTests(unittest.TestCase):
    def _safe_config(self, repo: Path) -> dict[str, object]:
        python_image = "candidate/python-integration:local"
        return {
            "services": {
                "gateway": {
                    "build": {"context": str(repo)},
                    "ports": [{"published": "8080", "target": 8080}],
                },
                "api": {"build": {"context": str(repo)}},
                "cli": {"image": "candidate/api:local"},
                "postgres": {"image": "postgres:16"},
                "worker-a": {"build": {"context": str(repo)}},
                "worker-b": {"image": "candidate/worker:local"},
                "outbox-dispatcher": {
                    "image": python_image,
                    "build": {"context": str(repo)},
                    "environment": {"PGUSER": "outbox_dispatcher"},
                },
                "receipt-adapter": {
                    "image": python_image,
                    "environment": {"RECEIPT_API_URL": "http://gateway:8080"},
                },
                "inbox-reconciler": {
                    "image": python_image,
                    "environment": {"PGUSER": "inbox_reconciler"},
                },
                "provider-simulator": {
                    "image": public_check.PROVIDER_IMAGE,
                    "environment": {
                        "CALLBACK_URL": (
                            "http://receipt-adapter:8082/callbacks/provider-v02/"
                        )
                    },
                },
            },
            "volumes": {"data": {}},
        }

    def test_safe_compose_contract_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            findings = public_check._compose_contract_findings(
                self._safe_config(repo), repo
            )
            self.assertEqual(findings, [])

    def test_gateway_default_published_port_is_detected(self) -> None:
        service = {"ports": ["127.0.0.1:${COURSE_GATEWAY_PORT:-8080}:8080"]}
        self.assertEqual(public_check._published_ports(service), {8080})
        self.assertEqual(
            public_check._published_ports({"ports": ["127.0.0.1:18080:8080"]}),
            {18080},
        )
        self.assertEqual(
            public_check._published_ports({"ports": ["18080-18082:8080-8082"]}),
            {18080, 18081, 18082},
        )

    def test_source_and_runtime_gateway_ports_are_checked_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            source = self._safe_config(repo)
            self.assertEqual(public_check._compose_contract_findings(source, repo), [])
            runtime = json.loads(json.dumps(source))
            runtime["services"]["gateway"]["ports"] = [
                {"host_ip": "127.0.0.1", "published": "43123", "target": 8080}
            ]
            self.assertEqual(public_check._runtime_compose_findings(runtime, 43123), [])
            self.assertTrue(public_check._compose_contract_findings(runtime, repo))

    def test_candidate_config_forces_contract_port_before_runtime_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=43123,
                environment={"COURSE_GATEWAY_PORT": "43123"},
                sensitive=(),
            )
            response = public_check.CommandResult(("compose",), 0, "{}", "")
            with mock.patch.object(harness, "compose", return_value=response) as compose:
                harness.candidate_config()
            self.assertEqual(
                compose.call_args.kwargs["environment"],
                {"COURSE_GATEWAY_PORT": "8080"},
            )

    def test_digest_only_provider_reference_is_accepted(self) -> None:
        digest = public_check.PROVIDER_IMAGE.rsplit("@", 1)[1]
        reference = "ghcr.io/fintech-dev-lab/internship-provider-simulator@" + digest
        self.assertTrue(public_check._provider_image_matches(reference))

    def test_prebuilt_or_split_python_images_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["outbox-dispatcher"].pop("build")
            services["receipt-adapter"]["image"] = "candidate/other:local"
            findings = public_check._compose_contract_findings(config, repo)
            rendered = "\n".join(findings)
            self.assertIn("one shared image", rendered)
            self.assertIn("locally built", rendered)

    def test_adapter_database_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["receipt-adapter"]["environment"]["DATABASE_URL"] = "hidden"
            findings = public_check._compose_contract_findings(config, repo)
            self.assertTrue(
                any("PostgreSQL configuration" in item for item in findings)
            )

    def test_adapter_credential_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["receipt-adapter"]["env_file"] = ["adapter.env"]
            findings = public_check._compose_contract_findings(config, repo)
            self.assertTrue(any("host-backed service" in item for item in findings))

    def test_host_backed_compose_resources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["api"]["build"] = {
                "context": str(repo),
                "dockerfile": "/tmp/external.Dockerfile",
                "additional_contexts": {"home": "/home/user"},
            }
            config["secrets"] = {
                "docker-auth": {"file": "/home/user/.docker/config.json"}
            }
            findings = public_check._compose_contract_findings(config, repo)
            rendered = "\n".join(findings)
            self.assertIn("external additional build context", rendered)
            self.assertIn("external Dockerfile", rendered)
            self.assertIn("host-backed resources", rendered)

    def test_repository_local_additional_context_and_parent_dockerfile_are_accepted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["api"]["build"] = {
                "context": str(repo / "src"),
                "dockerfile": "../Dockerfile",
                "additional_contexts": {"shared": str(repo / "shared")},
            }
            findings = public_check._compose_contract_findings(config, repo)
            self.assertFalse(any("build context" in item for item in findings))
            self.assertFalse(any("Dockerfile" in item for item in findings))

    def test_python_database_principals_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["outbox-dispatcher"]["environment"] = {
                "DATABASE_URL": "postgresql://postgres:secret@postgres/course"
            }
            findings = public_check._compose_contract_findings(config, repo)
            self.assertTrue(
                any(
                    "database principal must be outbox_dispatcher" in item
                    for item in findings
                )
            )

    def test_adapter_connection_timeout_is_not_database_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["receipt-adapter"]["environment"]["HTTP_CONNECTION_TIMEOUT"] = "5"
            findings = public_check._compose_contract_findings(config, repo)
            self.assertFalse(
                any("PostgreSQL configuration" in item for item in findings)
            )

    def test_adapter_libpq_dsn_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["receipt-adapter"]["environment"]["DB_DSN"] = (
                "host=postgres dbname=course user=postgres password=secret"
            )
            findings = public_check._compose_contract_findings(config, repo)
            self.assertTrue(
                any("PostgreSQL configuration" in item for item in findings)
            )

    def test_synthetic_secret_distribution_is_restricted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            secret = "synthetic-hmac-secret"
            services["receipt-adapter"]["environment"]["PROVIDER_HMAC_SECRET"] = secret
            services["outbox-dispatcher"]["environment"]["LEAK"] = secret
            services["api"]["build"] = {
                "context": str(repo),
                "args": {"LEAK": secret},
            }
            findings = public_check._secret_distribution_findings(
                config,
                {
                    "provider-hmac-secret": (
                        secret,
                        {
                            ("api", "PROVIDER_HMAC_SECRET"),
                            ("receipt-adapter", "PROVIDER_HMAC_SECRET"),
                        },
                    )
                },
            )
            self.assertEqual(len(findings), 2)
            self.assertTrue(any("outbox-dispatcher.LEAK" in item for item in findings))
            self.assertTrue(any("outside api environment" in item for item in findings))

    def test_database_secret_allows_recipient_environment_only(self) -> None:
        secret = "synthetic-database-secret"
        config = {
            "services": {
                "postgres": {"environment": {"ROLE_PASSWORD": secret}},
                "worker-a": {"environment": {"COURSE_DB_PASSWORD": secret}},
                "receipt-adapter": {"environment": {"LEAK": secret}},
            }
        }
        findings = public_check._secret_distribution_findings(
            config,
            {
                "worker-password": (
                    secret,
                    {("postgres", "*"), ("worker-a", "*")},
                )
            },
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("receipt-adapter.LEAK", findings[0])

    def test_cli_is_declared_but_not_required_to_keep_running(self) -> None:
        self.assertIn("cli", public_check.REQUIRED_SERVICES)
        self.assertNotIn("cli", public_check.RUNNING_SERVICES)

    def test_host_escape_and_extra_ports_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["receipt-adapter"]["privileged"] = True
            services["receipt-adapter"]["ports"] = [
                {"published": "8082", "target": 8082}
            ]
            services["outbox-dispatcher"]["volumes"] = ["/var/run/docker.sock:/x"]
            findings = public_check._compose_contract_findings(config, repo)
            rendered = "\n".join(findings)
            self.assertIn("host/elevated", rendered)
            self.assertIn("only gateway", rendered)
            self.assertIn("bind mount", rendered)
            self.assertIn("Docker socket", rendered)

    def test_short_port_publication_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            config = self._safe_config(repo)
            services = config["services"]
            assert isinstance(services, dict)
            services["receipt-adapter"]["ports"] = ["8082"]
            findings = public_check._compose_contract_findings(config, repo)
            self.assertTrue(any("only gateway" in item for item in findings))

    def test_callback_base_preserves_candidate_internal_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._safe_config(Path(temporary))
            services = config["services"]
            assert isinstance(services, dict)
            services["provider-simulator"]["environment"]["CALLBACK_URL"] = (
                "http://receipt-adapter:9123/callbacks/provider-v02/candidate"
            )
            self.assertEqual(
                public_check._provider_callback_base(config),
                "http://receipt-adapter:9123/callbacks/provider-v02",
            )

    def test_override_configures_all_csharp_application_services(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = public_check.PublicChecker.__new__(public_check.PublicChecker)
            checker.override = root / "override.yaml"
            checker.project = "public-test"
            checker.callback_base = "http://receipt-adapter:9123/callbacks/provider-v02"
            checker._write_override(self._safe_config(root))
            rendered = checker.override.read_text(encoding="utf-8")
            for service in ("gateway", "api", "cli", "worker-a", "worker-b"):
                remainder = rendered.split(f"  {service}:\n", 1)[1]
                section_lines: list[str] = []
                for line in remainder.splitlines():
                    if line.startswith("  ") and not line.startswith("    "):
                        break
                    section_lines.append(line)
                section = "\n".join(section_lines)
                self.assertIn("COURSE_JWT_ISSUER", section)
                self.assertIn("COURSE_JWT_AUDIENCE", section)
                self.assertIn("COURSE_JWT_SIGNING_KEY", section)
                self.assertIn("COURSE_TEST_PROFILE", section)
            self.assertIn(
                "http://receipt-adapter:9123/callbacks/provider-v02/"
                "${PROVIDER_CALLBACK_CAPABILITY}",
                rendered,
            )

    def test_internal_http_probe_script_is_valid_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=8080,
                environment={},
                sensitive=(),
            )
            response = public_check.CommandResult(
                ("compose",),
                0,
                '{"status":204,"data":"","headers":{}}\n',
                "",
            )
            with mock.patch.object(
                harness, "compose", return_value=response
            ) as compose:
                result = harness.internal_http(
                    "receipt-adapter",
                    "POST",
                    "http://127.0.0.1:8082/callbacks/provider-v02/test",
                    body={"value": True},
                )
            self.assertEqual(result.status, 204)
            script = compose.call_args.args[0][5]
            compile(script, "<internal-http-probe>", "exec")

    def test_python_runtime_detection_accepts_312(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=8080,
                environment={},
                sensitive=(),
            )
            process = public_check.CommandResult(
                ("docker", "inspect"),
                0,
                "/usr/local/bin/python3.12\n",
                "",
            )
            version = public_check.CommandResult(
                ("docker", "exec"), 0, "Python 3.12.7\n", ""
            )
            with (
                mock.patch.object(harness, "container_id", return_value="a" * 64),
                mock.patch.object(harness, "run", side_effect=[process, version]),
            ):
                actual = harness.detect_python_runtime("receipt-adapter")
            self.assertEqual(actual, (3, 12, 7))
            self.assertEqual(
                harness.python_executables["receipt-adapter"],
                "/usr/local/bin/python3.12",
            )

    def test_python_runtime_detection_rejects_dormant_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=8080,
                environment={},
                sensitive=(),
            )
            process = public_check.CommandResult(
                ("docker", "inspect"),
                0,
                "dotnet\n",
                "",
            )
            with (
                mock.patch.object(harness, "container_id", return_value="a" * 64),
                mock.patch.object(harness, "run", return_value=process),
                self.assertRaises(public_check.ContractError),
            ):
                harness.detect_python_runtime("receipt-adapter")

    def test_main_process_detection_accepts_init_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=8080,
                environment={},
                sensitive=(),
            )
            responses = [
                public_check.CommandResult(("docker", "inspect"), 0, "tini\n", ""),
                public_check.CommandResult(("docker", "inspect"), 0, "100\n", ""),
                public_check.CommandResult(
                    ("docker", "top"),
                    0,
                    "PID PPID COMMAND\n100 1 tini\n101 100 python3.12\n",
                    "",
                ),
            ]
            with (
                mock.patch.object(harness, "container_id", return_value="a" * 64),
                mock.patch.object(harness, "run", side_effect=responses),
            ):
                self.assertEqual(
                    harness.main_process_executable("receipt-adapter"), "python3.12"
                )

    def test_process_probe_requests_pid_for_docker_top(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=8080,
                environment={},
                sensitive=(),
            )
            response = public_check.CommandResult(
                ("docker", "top"),
                0,
                "PID COMMAND COMMAND\n1 python python -m app\n",
                "",
            )
            with (
                mock.patch.object(harness, "container_id", return_value="a" * 64),
                mock.patch.object(harness, "run", return_value=response) as run,
            ):
                self.assertIn("python", harness.process_text("receipt-adapter"))
            self.assertEqual(run.call_args.args[0][-2:], ("-eo", "pid,comm,args"))

    def test_candidate_http_transport_failure_is_contract_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = public_check.ComposeHarness(
                repo=root,
                compose_file=root / "compose.yaml",
                override_file=root / "override.yaml",
                wrapper=root / "wrapper.sh",
                project="project",
                gateway_port=8080,
                environment={},
                sensitive=(),
            )
            error = public_check.urllib.error.URLError("connection refused")
            with mock.patch.object(
                public_check.urllib.request, "urlopen", side_effect=error
            ):
                with self.assertRaises(public_check.ContractError):
                    harness.http("GET", "/health/ready")


class PollingTests(unittest.TestCase):
    def test_poll_timeout_rejects_stale_rows(self) -> None:
        checker = public_check.PublicChecker.__new__(public_check.PublicChecker)
        checker.harness = mock.Mock()
        checker.harness.psql_rows.return_value = [{"status": "PROCESSING"}]
        with (
            mock.patch.object(
                public_check.time, "monotonic", side_effect=[0.0, 0.1, 2.0]
            ),
            mock.patch.object(public_check.time, "sleep"),
            self.assertRaisesRegex(
                public_check.ContractError, "timed out waiting for candidate evidence"
            ),
        ):
            checker._poll_rows(
                "SELECT status",
                lambda rows: rows[0].get("status") == "COMPLETED",
                timeout=1.0,
            )


class ActionResultTests(unittest.TestCase):
    def test_repeat_ignores_transport_meta_but_not_business_result(self) -> None:
        first = {
            "status": "ok",
            "outcome": "APPROVED",
            "result": {"decision": "APPROVED"},
            "meta": {"correlationId": "first"},
        }
        repeated = {**first, "meta": {"correlationId": "second"}}
        changed = {**repeated, "result": {"decision": "REJECTED"}}
        self.assertTrue(public_check.same_action_result(first, repeated))
        self.assertFalse(public_check.same_action_result(first, changed))


class ProjectionContractTests(unittest.TestCase):
    def test_published_column_types_are_explicit(self) -> None:
        self.assertEqual(public_check._expected_column_type("process_id"), "uuid")
        self.assertEqual(
            public_check._expected_column_type("received_at"),
            "timestamp with time zone",
        )
        self.assertEqual(public_check._expected_column_type("lease_version"), "bigint")
        self.assertEqual(public_check._expected_column_type("outcomes"), "jsonb")
        self.assertEqual(public_check._expected_column_type("message_id"), "text")


class MachineArtifactTests(unittest.TestCase):
    def test_all_json_contracts_are_objects(self) -> None:
        for path in (PACKAGE / "contracts" / "course-1").glob("*.json"):
            with self.subTest(path=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertIsInstance(value, dict)

    def test_score_manifest_sums_to_25(self) -> None:
        path = PACKAGE / "contracts" / "course-1" / "week-3-score-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifestVersion"], "week-3.1")
        self.assertEqual(sum(item["weight"] for item in manifest["criteria"]), 25)
        self.assertEqual(
            {item["id"] for item in manifest["criteria"]},
            {"BNK-01", "BNK-02", "BNK-03", "BNK-04", "BNK-05", "PG-02", "PG-05"},
        )
    def test_published_schema_set_is_complete(self) -> None:
        names = [
            "payment-submit.payload.schema.json",
            "payment-submit.result.schema.json",
            "workflow-manual.payload.schema.json",
            "workflow-manual.result.schema.json",
            "provider-v02-payment-request.schema.json",
            "provider-v02-callback.schema.json",
            "receipt-v1.schema.json",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertIsInstance(
                    json.loads(
                        (PACKAGE / "contracts" / "course-1" / name).read_text(
                            encoding="utf-8"
                        )
                    ),
                    dict,
                )

    def test_crlf_is_rejected_at_every_position(self) -> None:
        protected = {
            "provider-v02-payment-request.schema.json": ("operationId",),
            "provider-v02-callback.schema.json": (
                "providerPaymentId",
                "operationId",
                "message",
                "occurredAt",
            ),
            "receipt-v1.schema.json": (
                "externalRequestId",
                "messageId",
                "occurredAt",
                "providerPaymentId",
            ),
        }
        for name, fields in protected.items():
            schema = json.loads(
                (PACKAGE / "contracts" / "course-1" / name).read_text(
                    encoding="utf-8"
                )
            )
            for field in fields:
                pattern = schema["properties"][field]["not"]["pattern"]
                self.assertIsNone(re.search(pattern, "valid-value"))
                for value in ("\rprefix", "middle\nvalue", "suffix\r"):
                    with self.subTest(schema=name, field=field, value=repr(value)):
                        self.assertIsNotNone(re.search(pattern, value))

    def test_provider_payment_id_is_required_string(self) -> None:
        schema = json.loads(
            (PACKAGE / "contracts" / "course-1" / "receipt-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["providerPaymentId"]["type"], "string")


class WrapperTests(unittest.TestCase):
    def test_wrapper_documents_external_repository_argument(self) -> None:
        completed = subprocess.run(
            [str(PACKAGE / "check.sh"), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--repo PATH", completed.stdout)

    def test_safe_compose_passes_documented_configuration(self) -> None:
        script = (PACKAGE / "autocheck" / "safe_compose.sh").read_text(
            encoding="utf-8"
        )
        for name in (
            "COURSE_POSTGRES_PASSWORD",
            "COURSE_OUTBOX_PASSWORD",
            "COURSE_INBOX_PASSWORD",
            "PROVIDER_URL",
            "OUTBOX_OWNER",
            "RECEIPT_API_URL",
        ):
            with self.subTest(name=name):
                self.assertIn(f'{name}="${{{name}:-}}"', script)


class ReportTests(unittest.TestCase):
    def test_report_shape_and_redaction(self) -> None:
        secret = "synthetic-secret-value"
        report = public_check.build_report(
            started_at="start",
            finished_at="finish",
            status="passed",
            checks=[
                {
                    "name": "safe",
                    "phase": "security",
                    "status": "passed",
                    "expected": True,
                    "actual": public_check._redact(f"prefix {secret}", (secret,)),
                }
            ],
            commands=[],
        )
        self.assertFalse(public_check.report_has_forbidden_keys(report))
        self.assertNotIn(secret, json.dumps(report))
        self.assertTrue(public_check.report_has_forbidden_keys({"sco" + "re": 1}))

    def test_report_write_replaces_hardlink_without_truncating_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("sentinel\n", encoding="utf-8")
            report_path = root / "report.json"
            report_path.hardlink_to(target)
            public_check._write_report(report_path, {"status": "passed"})
            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8")),
                {"status": "passed"},
            )

    def test_report_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("sentinel\n", encoding="utf-8")
            report_path = root / "report.json"
            report_path.symlink_to(target)
            with self.assertRaises(public_check.EnvironmentFailure):
                public_check._write_report(report_path, {"status": "passed"})
            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")


class ExitCodeTests(unittest.TestCase):
    def test_missing_candidate_compose_is_contract_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = public_check.main(
                    [
                        "--repo",
                        str(root),
                        "--fixtures",
                        str(FIXTURES),
                        "--output",
                        str(output),
                        "--compose-wrapper",
                        str(MODULE_PATH.with_name("safe_compose.sh")),
                    ]
                )
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "failed")


if __name__ == "__main__":
    unittest.main()
