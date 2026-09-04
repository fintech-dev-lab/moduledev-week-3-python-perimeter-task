#!/usr/bin/env python3
"""Run the published week-3 Python perimeter black-box checks."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


MANIFEST_VERSION = "week-3-public-report/v1"
TOOL_VERSION = "week-3-public-check/0.2"
PUBLISHED_FIXTURE_DIGEST = (
    "1f83c074ea60ae3941f75dd48e1526030ebdd5e467a66753e1c6a1c06c67c059"
)
PUBLISHED_FIXTURE_GENERATOR = "week-3.1"
PROVIDER_IMAGE = (
    "ghcr.io/fintech-dev-lab/internship-provider-simulator:v0.2.0@"
    "sha256:70e5e0dd9ab8425be84de431ec74516f9bedf5d5529077358e2e2b2037fe0c74"
)
COMPOSE_NAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
)
REQUIRED_SERVICES = {
    "gateway",
    "api",
    "cli",
    "postgres",
    "worker-a",
    "worker-b",
    "outbox-dispatcher",
    "receipt-adapter",
    "inbox-reconciler",
    "provider-simulator",
}
PYTHON_SERVICES = (
    "outbox-dispatcher",
    "receipt-adapter",
    "inbox-reconciler",
)
CSHARP_SERVICES = ("gateway", "api", "worker-a", "worker-b")
REQUIRED_VIEWS = {
    "contract_info",
    "action_definitions",
    "action_dispatches",
    "operations",
    "operation_events",
    "flow_versions",
    "processes",
    "steps",
    "jobs",
    "attempts",
    "signals",
    "workflow_events",
    "external_requests",
    "receipts",
    "outbox",
    "inbox",
    "decisions",
}
REQUIRED_VIEW_COLUMNS = {
    "contract_info": {"contract_version", "generated_at"},
    "action_definitions": {
        "module",
        "action",
        "version",
        "http_method",
        "target_schema",
        "target_function",
        "outcomes",
        "enabled",
        "is_default",
    },
    "action_dispatches": {
        "correlation_id",
        "request_id",
        "module",
        "action",
        "version",
        "principal",
        "payload_hash",
        "status",
        "outcome",
        "occurred_at",
    },
    "operations": {
        "operation_id",
        "request_id",
        "operation_kind",
        "amount",
        "currency",
        "status",
        "process_id",
        "created_at",
        "updated_at",
    },
    "operation_events": {
        "event_id",
        "operation_id",
        "event_type",
        "payload_hash",
        "occurred_at",
    },
    "flow_versions": {
        "flow_name",
        "flow_version",
        "status",
        "is_active",
        "published_at",
    },
    "processes": {
        "process_id",
        "business_key",
        "flow_name",
        "flow_version",
        "state",
        "current_step_key",
        "created_at",
        "updated_at",
    },
    "steps": {
        "step_instance_id",
        "process_id",
        "step_key",
        "step_type",
        "state",
        "outcome",
        "entered_at",
        "completed_at",
    },
    "jobs": {
        "job_id",
        "process_id",
        "step_instance_id",
        "execution_id",
        "state",
        "lease_owner",
        "lease_version",
        "lease_until",
        "attempt_count",
        "next_attempt_at",
    },
    "attempts": {
        "attempt_id",
        "job_id",
        "execution_id",
        "lease_version",
        "attempt_number",
        "status",
        "outcome",
        "error_code",
        "started_at",
        "finished_at",
    },
    "signals": {
        "message_id",
        "process_id",
        "signal_type",
        "body_hash",
        "status",
        "received_at",
    },
    "workflow_events": {
        "event_id",
        "process_id",
        "step_instance_id",
        "event_type",
        "occurred_at",
    },
    "external_requests": {
        "external_request_id",
        "operation_id",
        "state",
        "payload_hash",
        "created_at",
    },
    "receipts": {
        "message_id",
        "external_request_id",
        "message_version",
        "outcome",
        "signature_valid",
        "body_hash",
        "received_at",
        "applied_at",
    },
    "outbox": {
        "outbox_id",
        "external_request_id",
        "state",
        "attempt_count",
        "next_attempt_at",
        "last_error_code",
        "created_at",
        "delivered_at",
    },
    "inbox": {"message_id", "body_hash", "state", "received_at", "applied_at"},
    "decisions": {
        "decision_id",
        "process_id",
        "step_instance_id",
        "source",
        "principal",
        "reason_hash",
        "outcome",
        "rule_version",
        "created_at",
    },
}
UUID_COLUMNS = {
    "attempt_id",
    "correlation_id",
    "decision_id",
    "event_id",
    "execution_id",
    "job_id",
    "operation_id",
    "outbox_id",
    "process_id",
    "step_instance_id",
}
TIMESTAMPTZ_COLUMNS = {
    "applied_at",
    "completed_at",
    "created_at",
    "delivered_at",
    "entered_at",
    "finished_at",
    "generated_at",
    "lease_until",
    "next_attempt_at",
    "occurred_at",
    "published_at",
    "received_at",
    "started_at",
    "updated_at",
}
INTEGER_COLUMNS = {
    "attempt_count",
    "attempt_number",
    "flow_version",
    "message_version",
    "version",
}
BIGINT_COLUMNS = {"lease_version"}
BOOLEAN_COLUMNS = {"enabled", "is_active", "is_default", "signature_valid"}
JSONB_COLUMNS = {"outcomes"}
NUMERIC_COLUMNS = {"amount"}
FIXTURE_FILES = {
    "providerRequest",
    "adapterRequest",
    "reviewAutoRequest",
    "reviewManualRequest",
}
_SECRET_KEY = re.compile(
    r"(?:authorization|password|secret|token|signature|capability|payload|body)$",
    re.IGNORECASE,
)


class FixtureError(ValueError):
    """Trusted fixture metadata or content is invalid."""


class ContractError(RuntimeError):
    """Candidate behavior violates the published contract."""


class EnvironmentFailure(RuntimeError):
    """The local trusted checking environment cannot execute the scenario."""


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    headers: dict[str, str]

    def json(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_token(
    secret: str,
    subject: str,
    consumer: str,
    scopes: Sequence[str],
    *,
    issuer: str = "moduledev-course",
    audience: str = "moduledev-api",
) -> str:
    now = int(time.time())
    header = _base64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = _base64url(
        json.dumps(
            {
                "iss": issuer,
                "aud": audience,
                "sub": subject,
                "consumer": consumer,
                "scope": " ".join(scopes),
                "iat": now - 5,
                "exp": now + 3600,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _base64url(
        hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    )
    return f"{header}.{payload}.{signature}"


def normalized_receipt(legacy: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "providerPaymentId",
        "operationId",
        "result",
        "message",
        "occurredAt",
    }
    if set(legacy) != expected:
        raise ValueError("legacy callback fields do not match provider v0.2.0")
    for field in ("providerPaymentId", "operationId", "occurredAt"):
        value = legacy[field]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError(f"invalid legacy {field}")
    if legacy["result"] not in {"COMPLETED", "REJECTED"}:
        raise ValueError("invalid legacy result")
    message = legacy["message"]
    if not isinstance(message, str) or len(message) > 500:
        raise ValueError("invalid legacy message")
    return {
        "externalRequestId": legacy["operationId"],
        "messageId": legacy["providerPaymentId"],
        "occurredAt": legacy["occurredAt"],
        "outcome": legacy["result"],
        "providerPaymentId": legacy["providerPaymentId"],
        "version": 1,
    }


def receipt_bytes(receipt: dict[str, Any]) -> bytes:
    return json.dumps(
        receipt,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def receipt_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def canonical_fixture_digest(root: Path) -> str:
    metadata_path = root / "fixture.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError("fixture.json is unreadable") from error
    if not isinstance(metadata, dict):
        raise FixtureError("fixture.json must be an object")
    canonical_metadata = dict(metadata)
    canonical_metadata["digest"] = ""
    hasher = hashlib.sha256()
    hasher.update(b"fixture.json\0")
    hasher.update(
        json.dumps(
            canonical_metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    files = metadata.get("files")
    if not isinstance(files, dict) or set(files) != FIXTURE_FILES:
        raise FixtureError("fixture file map is invalid")
    for relative in sorted(files.values()):
        if not isinstance(relative, str):
            raise FixtureError("fixture file path is invalid")
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as error:
            raise FixtureError("fixture path escapes its root") from error
        if not path.is_file():
            raise FixtureError(f"fixture file is missing: {relative}")
        hasher.update(b"\0")
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def load_fixture(root: Path) -> dict[str, Any]:
    try:
        fixture = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureError("fixture metadata is unreadable") from error
    if not isinstance(fixture, dict):
        raise FixtureError("fixture metadata must be an object")
    if fixture.get("generatorVersion") != PUBLISHED_FIXTURE_GENERATOR:
        raise FixtureError("fixture generator version is not published")
    if fixture.get("contractVersion") != "course-1":
        raise FixtureError("fixture contract version is invalid")
    actual = canonical_fixture_digest(root)
    if fixture.get("digest") != actual or actual != PUBLISHED_FIXTURE_DIGEST:
        raise FixtureError("fixture digest does not match the published package")
    if fixture.get("provider", {}).get("image") != PROVIDER_IMAGE:
        raise FixtureError("provider image is not the published digest")
    if set(fixture.get("services", [])) != REQUIRED_SERVICES:
        raise FixtureError("fixture service seam is invalid")
    for relative in fixture["files"].values():
        value = json.loads((root / relative).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise FixtureError(f"fixture payload is not an object: {relative}")
    return fixture


def _redact(value: Any, sensitive: Sequence[str], depth: int = 0) -> Any:
    if depth > 12:
        return "[depth-limit]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                result[str(key)] = "[redacted]"
            else:
                result[str(key)] = _redact(item, sensitive, depth + 1)
        return result
    if isinstance(value, list):
        return [_redact(item, sensitive, depth + 1) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, sensitive, depth + 1) for item in value]
    if isinstance(value, set):
        return sorted(
            (_redact(item, sensitive, depth + 1) for item in value),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    if isinstance(value, str):
        result = value
        for secret in sensitive:
            if secret:
                result = result.replace(secret, "[redacted]")
        return result[:2000]
    return value


def report_has_forbidden_keys(value: Any) -> bool:
    forbidden = re.compile(
        r"(?:score|earned|points|criterion|hmac|secret|token|signature|capability|payload|body)",
        re.IGNORECASE,
    )
    if isinstance(value, dict):
        return any(
            forbidden.search(str(key)) or report_has_forbidden_keys(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(report_has_forbidden_keys(item) for item in value)
    return False


def build_report(
    *,
    started_at: str,
    finished_at: str,
    status: str,
    checks: list[dict[str, Any]],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "manifestVersion": MANIFEST_VERSION,
        "toolVersion": TOOL_VERSION,
        "timestamps": {"startedAt": started_at, "finishedAt": finished_at},
        "status": status,
        "checks": checks,
        "failedChecks": [
            item["name"] for item in checks if item.get("status") == "failed"
        ],
        "commands": commands,
    }


def _published_ports(service: Any) -> set[int]:
    result: set[int] = set()
    if not isinstance(service, dict):
        return result
    for item in service.get("ports", []) or []:
        value: Any = None
        if isinstance(item, dict):
            value = item.get("published")
        elif isinstance(item, str):
            without_protocol = item.split("/", 1)[0]
            default_port = re.search(r":-(\d+)}(?=:)", without_protocol)
            if default_port:
                value = default_port.group(1)
            else:
                parts = without_protocol.rsplit(":", 2)
                if len(parts) >= 2:
                    value = parts[-2]
        if isinstance(value, str):
            default_port = re.fullmatch(r"\$\{[^}]+:-(\d+)}", value)
            if default_port:
                value = default_port.group(1)
        if isinstance(value, str) and re.fullmatch(r"\d+-\d+", value):
            start, end = (int(part) for part in value.split("-", 1))
            if start <= end and end - start <= 1000:
                result.update(range(start, end + 1))
            continue
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _service_environment(service: Any) -> dict[str, str]:
    if not isinstance(service, dict):
        return {}
    environment = service.get("environment", {}) or {}
    if isinstance(environment, dict):
        return {
            str(key): "" if value is None else str(value)
            for key, value in environment.items()
        }
    if isinstance(environment, list):
        result: dict[str, str] = {}
        for item in environment:
            key, separator, value = str(item).partition("=")
            result[key] = value if separator else ""
        return result
    return {}


def _path_is_within(path: Any, root: Path, *, base: Path | None = None) -> bool:
    if (
        not isinstance(path, str)
        or not path
        or re.match(r"^(?:[a-z]+://|git@)", path, re.I)
    ):
        return False
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (base or root) / candidate
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _database_environment_entries(environment: dict[str, str]) -> dict[str, str]:
    key_pattern = re.compile(
        r"(?:^|_)(?:PG(?:HOST|PORT|DATABASE|USER|PASSWORD|SERVICE)|"
        r"DB_(?:HOST|PORT|NAME|USER|USERNAME|PASSWORD)|"
        r"DB_(?:URL|URI|DSN)|"
        r"DATABASE_(?:HOST|PORT|NAME|USER|USERNAME|PASSWORD|URL|URI|DSN)|"
        r"POSTGRES(?:QL)?_(?:HOST|PORT|DB|DATABASE|USER|USERNAME|PASSWORD|URL|URI|DSN)|"
        r"PGDSN|CONNECTION_STRING|DATABASE_CONNECTION_STRING)(?:$|_)",
        re.I,
    )
    result = {
        key: value for key, value in environment.items() if key_pattern.search(key)
    }
    for key, value in environment.items():
        if re.match(r"^postgres(?:ql)?://", value, re.I):
            result[key] = value
        if re.search(
            r"(?:^|\s)(?:host|hostaddr|port|dbname|user|password|service)=", value, re.I
        ):
            result[key] = value
    return result


def _database_principals(environment: dict[str, str]) -> set[str]:
    principals: set[str] = set()
    user_key = re.compile(
        r"(?:^|_)(?:PGUSER|DB_USER|DB_USERNAME|DATABASE_USER|DATABASE_USERNAME|"
        r"POSTGRES_USER|POSTGRES_USERNAME)(?:$|_)",
        re.I,
    )
    for key, value in _database_environment_entries(environment).items():
        if user_key.search(key) and value:
            principals.add(value)
        if re.match(r"^postgres(?:ql)?://", value, re.I):
            try:
                username = urllib.parse.urlsplit(value).username
            except ValueError:
                username = None
            if username:
                principals.add(urllib.parse.unquote(username))
    return principals


def _expected_column_type(column: str) -> str:
    if column in UUID_COLUMNS:
        return "uuid"
    if column in TIMESTAMPTZ_COLUMNS:
        return "timestamp with time zone"
    if column in INTEGER_COLUMNS:
        return "integer"
    if column in BIGINT_COLUMNS:
        return "bigint"
    if column in BOOLEAN_COLUMNS:
        return "boolean"
    if column in JSONB_COLUMNS:
        return "jsonb"
    if column in NUMERIC_COLUMNS:
        return "numeric"
    return "text"


def _secret_distribution_findings(
    config: dict[str, Any],
    secrets_by_name: dict[str, tuple[str, set[tuple[str, str]]]],
) -> list[str]:
    findings: list[str] = []
    services = config.get("services", {})
    if not isinstance(services, dict):
        return ["services must be an object"]
    top_level = dict(config)
    top_level.pop("services", None)
    top_level_text = json.dumps(top_level, ensure_ascii=False)
    for label, (secret, allowed_locations) in secrets_by_name.items():
        if not secret:
            continue
        if secret in top_level_text:
            findings.append(f"{label}: secret is exposed outside service environment")
        for service_name, service in services.items():
            if not isinstance(service, dict):
                continue
            environment = _service_environment(service)
            for key, value in environment.items():
                if (
                    secret in value
                    and (str(service_name), key) not in allowed_locations
                ):
                    findings.append(
                        f"{label}: secret is exposed to {service_name}.{key}"
                    )
            non_environment = dict(service)
            non_environment.pop("environment", None)
            if secret in json.dumps(non_environment, ensure_ascii=False):
                findings.append(
                    f"{label}: secret is exposed outside {service_name} environment"
                )
    return findings


def _provider_image_matches(value: Any) -> bool:
    if not isinstance(value, str) or "@" not in value:
        return False
    reference, digest = value.rsplit("@", 1)
    expected_reference, expected_digest = PROVIDER_IMAGE.rsplit("@", 1)
    expected_repository = expected_reference.rsplit(":", 1)[0]
    repository = (
        reference.rsplit(":", 1)[0]
        if ":" in reference.rsplit("/", 1)[-1]
        else reference
    )
    return repository == expected_repository and digest == expected_digest


def _provider_callback_base(config: dict[str, Any]) -> str:
    services = config.get("services", {})
    if not isinstance(services, dict):
        raise ContractError("services must be an object")
    provider = services.get("provider-simulator")
    callback = _service_environment(provider).get("CALLBACK_URL", "")
    try:
        parsed = urllib.parse.urlsplit(callback)
        port = parsed.port
    except ValueError as error:
        raise ContractError("provider CALLBACK_URL is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "receipt-adapter"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or re.fullmatch(r"/callbacks/provider-v02/[^/]*", parsed.path) is None
    ):
        raise ContractError(
            "provider CALLBACK_URL must target the receipt-adapter callback path"
        )
    authority = "receipt-adapter" if port in (None, 80) else f"receipt-adapter:{port}"
    return f"http://{authority}/callbacks/provider-v02"


def _unsafe_compose_findings(config: dict[str, Any], repo: Path) -> list[str]:
    findings: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return ["services must be an object"]
    for name, service in services.items():
        if not isinstance(service, dict):
            findings.append(f"{name}: service config is invalid")
            continue
        if service.get("privileged") or service.get("network_mode") == "host":
            findings.append(f"{name}: host/elevated runtime is forbidden")
        if service.get("pid") == "host" or service.get("ipc") == "host":
            findings.append(f"{name}: host namespace is forbidden")
        if service.get("devices") or service.get("cap_add"):
            findings.append(f"{name}: device/capability access is forbidden")
        if any(
            service.get(key)
            for key in (
                "configs",
                "credential_spec",
                "develop",
                "env_file",
                "label_file",
                "secrets",
                "volumes_from",
            )
        ):
            findings.append(f"{name}: host-backed service resources are forbidden")
        for volume in service.get("volumes", []) or []:
            if isinstance(volume, dict):
                source = str(volume.get("source", ""))
                volume_type = volume.get("type")
            else:
                source = str(volume).split(":", 1)[0]
                volume_type = "bind" if source.startswith((".", "/")) else "volume"
            if volume_type == "bind":
                findings.append(f"{name}: bind mount is forbidden")
            if "docker.sock" in source.lower():
                findings.append(f"{name}: Docker socket is forbidden")
        build = service.get("build")
        context = build.get("context", ".") if isinstance(build, dict) else build
        if build is not None and not _path_is_within(context, repo):
            findings.append(f"{name}: external build context is forbidden")
        if isinstance(build, dict):
            if (
                any(
                    build.get(key)
                    for key in (
                        "cache_from",
                        "cache_to",
                        "entitlements",
                        "privileged",
                        "secrets",
                        "ssh",
                    )
                )
                or build.get("network") == "host"
            ):
                findings.append(f"{name}: host-backed build resources are forbidden")
            additional_contexts = build.get("additional_contexts", {}) or {}
            if isinstance(additional_contexts, dict):
                additional_paths = additional_contexts.values()
            elif isinstance(additional_contexts, list):
                additional_paths = [
                    str(item).split("=", 1)[-1] for item in additional_contexts
                ]
            else:
                additional_paths = (None,)
            if any(not _path_is_within(path, repo) for path in additional_paths):
                findings.append(
                    f"{name}: external additional build context is forbidden"
                )
            dockerfile = build.get("dockerfile")
            context_root = Path(context) if isinstance(context, str) else repo
            if not context_root.is_absolute():
                context_root = repo / context_root
            if dockerfile is not None and not _path_is_within(
                dockerfile, repo, base=context_root
            ):
                findings.append(f"{name}: external Dockerfile is forbidden")
    for kind in ("volumes", "networks"):
        resources = config.get(kind, {}) or {}
        if not isinstance(resources, dict):
            findings.append(f"{kind}: resource map is invalid")
            continue
        for name, resource in resources.items():
            if isinstance(resource, dict) and resource.get("external"):
                findings.append(f"{kind}.{name}: external resource is forbidden")
            if isinstance(resource, dict) and resource.get("driver_opts"):
                findings.append(f"{kind}.{name}: driver options are forbidden")
    for kind in ("secrets", "configs"):
        if config.get(kind):
            findings.append(f"{kind}: host-backed resources are forbidden")
    return findings


def _compose_contract_findings(config: dict[str, Any], repo: Path) -> list[str]:
    findings = _unsafe_compose_findings(config, repo)
    services = config.get("services", {})
    if not isinstance(services, dict):
        return findings
    missing = sorted(REQUIRED_SERVICES - set(services))
    if missing:
        findings.append(f"missing services: {', '.join(missing)}")
        return findings
    for name, service in services.items():
        ports = _published_ports(service)
        if name == "gateway":
            if 8080 not in ports:
                findings.append("gateway must publish host port 8080")
        elif isinstance(service, dict) and service.get("ports"):
            findings.append(f"{name}: only gateway may publish host ports")
    provider = services["provider-simulator"]
    if not _provider_image_matches(provider.get("image")):
        findings.append("provider-simulator image must use the published digest")
    if provider.get("build"):
        findings.append("provider-simulator must not be locally built")
    python_images = {str(services[name].get("image", "")) for name in PYTHON_SERVICES}
    python_builds = [name for name in PYTHON_SERVICES if services[name].get("build")]
    if len(python_images) != 1 or not next(iter(python_images), ""):
        findings.append("Python integration services must declare one shared image")
    if not python_builds:
        findings.append("Python integration image must be locally built")
    if any(
        services[name].get("pull_policy") not in (None, "build")
        for name in PYTHON_SERVICES
    ):
        findings.append("Python integration services must run the locally built image")
    expected_roles = {
        "outbox-dispatcher": "outbox_dispatcher",
        "inbox-reconciler": "inbox_reconciler",
    }
    for name, expected_role in expected_roles.items():
        principals = _database_principals(_service_environment(services[name]))
        if principals != {expected_role}:
            findings.append(f"{name}: database principal must be {expected_role}")
    adapter = services["receipt-adapter"]
    adapter_environment = _service_environment(adapter)
    if _database_environment_entries(adapter_environment):
        findings.append("receipt-adapter must not receive PostgreSQL configuration")
    return findings


class ComposeHarness:
    def __init__(
        self,
        *,
        repo: Path,
        compose_file: Path,
        override_file: Path,
        wrapper: Path,
        project: str,
        gateway_port: int,
        environment: dict[str, str],
        sensitive: Sequence[str],
    ) -> None:
        self.repo = repo
        self.compose_file = compose_file
        self.override_file = override_file
        self.wrapper = wrapper
        self.project = project
        self.gateway_port = gateway_port
        self.environment = environment
        self.sensitive = tuple(sensitive)
        self.commands: list[dict[str, Any]] = []
        self.python_executables: dict[str, str] = {}

    def run(self, command: Sequence[str], *, timeout: float = 120) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                list(command),
                cwd=self.repo,
                env={**os.environ, **self.environment},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            result = CommandResult(
                tuple(command), completed.returncode, completed.stdout, completed.stderr
            )
        except subprocess.TimeoutExpired as error:
            stdout = (
                error.stdout.decode()
                if isinstance(error.stdout, bytes)
                else error.stdout
            )
            stderr = (
                error.stderr.decode()
                if isinstance(error.stderr, bytes)
                else error.stderr
            )
            result = CommandResult(
                tuple(command), 124, stdout or "", stderr or "", timed_out=True
            )
        safe_command = [
            "[redacted-argument]" if len(str(argument)) > 256 else str(argument)
            for argument in command
        ]
        self.commands.append(
            {
                "command": _redact(safe_command, self.sensitive),
                "exitCode": result.returncode,
                "timedOut": result.timed_out,
                "durationMs": int((time.monotonic() - started) * 1000),
            }
        )
        return result

    def require_docker_result(self, result: CommandResult, message: str) -> None:
        if result.ok:
            return
        probe = self.run(
            ("docker", "info", "--format", "{{.ServerVersion}}"), timeout=15
        )
        if not probe.ok:
            raise EnvironmentFailure("Docker daemon became unavailable")
        raise ContractError(message)

    def compose(
        self, args: Sequence[str], *, override: bool = True, timeout: float = 120
    ) -> CommandResult:
        command = [
            str(self.wrapper),
            "--project-name",
            self.project,
            "-f",
            str(self.compose_file),
        ]
        if override:
            command.extend(("-f", str(self.override_file)))
        command.extend(args)
        return self.run(command, timeout=timeout)

    def candidate_config(self) -> CommandResult:
        return self.compose(
            ("config", "--format", "json", "--no-env-resolution"),
            override=False,
            timeout=30,
        )

    def container_id(self, service: str) -> str:
        result = self.compose(("ps", "-q", service), timeout=15)
        value = result.stdout.strip()
        self.require_docker_result(
            result, f"service {service} has no running container"
        )
        if re.fullmatch(r"[a-f0-9]{12,64}", value) is None:
            raise ContractError(f"service {service} has no running container")
        return value

    def image_id(self, service: str) -> str:
        container = self.container_id(service)
        result = self.run(("docker", "inspect", "--format", "{{.Image}}", container))
        value = result.stdout.strip()
        self.require_docker_result(result, f"cannot inspect image for {service}")
        if re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None:
            raise ContractError(f"cannot inspect image for {service}")
        return value

    def local_image_id(self, image: str) -> str:
        result = self.run(
            ("docker", "image", "inspect", "--format", "{{.Id}}", image),
            timeout=15,
        )
        value = result.stdout.strip()
        self.require_docker_result(
            result, "cannot inspect the locally built Python image"
        )
        if re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None:
            raise ContractError("cannot inspect the locally built Python image")
        return value

    def main_process_executable(self, service: str) -> str:
        container = self.container_id(service)
        result = self.run(
            (
                "docker",
                "inspect",
                "--format",
                "{{.Path}}",
                container,
            ),
            timeout=15,
        )
        value = result.stdout.strip()
        self.require_docker_result(result, f"cannot inspect main process for {service}")
        if not value or "\n" in value or "\r" in value:
            raise ContractError(f"cannot inspect main process for {service}")
        configured_name = Path(value).name.lower()
        if configured_name == "dotnet" or re.fullmatch(
            r"python(?:3(?:\.\d+)?)?", configured_name
        ):
            return value
        pid_result = self.run(
            ("docker", "inspect", "--format", "{{.State.Pid}}", container),
            timeout=15,
        )
        self.require_docker_result(
            pid_result, f"cannot inspect main process for {service}"
        )
        root_pid = pid_result.stdout.strip()
        top_result = self.run(
            ("docker", "top", container, "-eo", "pid,ppid,comm"), timeout=15
        )
        self.require_docker_result(
            top_result, f"cannot inspect main process for {service}"
        )
        rows: list[tuple[str, str, str]] = []
        for line in top_result.stdout.splitlines()[1:]:
            parts = line.split(maxsplit=2)
            if len(parts) == 3:
                rows.append((parts[0], parts[1], parts[2]))
        root_rows = [row for row in rows if row[0] == root_pid]
        if len(root_rows) != 1:
            raise ContractError(f"cannot inspect main process for {service}")
        executable = root_rows[0][2]
        wrappers = {"ash", "bash", "dash", "docker-init", "dumb-init", "sh", "tini"}
        if Path(executable).name.lower() in wrappers:
            children = [row for row in rows if row[1] == root_pid]
            if len(children) != 1:
                raise ContractError(f"cannot identify workload process for {service}")
            executable = children[0][2]
        return executable

    def process_text(self, service: str) -> str:
        result = self.run(
            ("docker", "top", self.container_id(service), "-eo", "pid,comm,args"),
            timeout=15,
        )
        self.require_docker_result(result, f"cannot inspect process for {service}")
        return result.stdout.lower()

    def process_commands(self, service: str) -> tuple[str, ...]:
        lines = self.process_text(service).splitlines()
        commands: list[str] = []
        for line in lines[1:]:
            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                commands.append(parts[1])
        return tuple(commands)

    def detect_python_runtime(self, service: str) -> tuple[int, int, int]:
        container = self.container_id(service)
        executable = self.main_process_executable(service)
        if re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(executable).name) is None:
            raise ContractError(f"main process for {service} is not Python")
        result = self.run(
            ("docker", "exec", container, executable, "--version"),
            timeout=15,
        )
        match = re.search(
            r"Python\s+(\d+)\.(\d+)\.(\d+)",
            result.stdout + "\n" + result.stderr,
        )
        self.require_docker_result(
            result, f"cannot determine Python runtime for {service}"
        )
        if match is None:
            raise ContractError(f"cannot determine Python runtime for {service}")
        self.python_executables[service] = executable
        return tuple(int(value) for value in match.groups())

    def http(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> HttpResult:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.gateway_port}{path}",
            method=method,
            data=body,
            headers=headers or {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResult(
                    response.status,
                    response.read(1024 * 1024),
                    dict(response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            return HttpResult(
                error.code,
                error.read(1024 * 1024),
                dict(error.headers.items()),
            )
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            raise ContractError(f"candidate HTTP transport failed: {error}") from error

    def wait_gateway(self, timeout: float = 300) -> None:
        deadline = time.monotonic() + timeout
        last = 0
        while time.monotonic() < deadline:
            try:
                response = self.http("GET", "/health/ready", timeout=2)
                last = response.status
                if response.status == 200:
                    return
            except ContractError:
                pass
            time.sleep(0.25)
        raise ContractError(f"gateway readiness did not become 200 (last={last})")

    def action(
        self,
        module: str,
        action: str,
        payload: dict[str, Any],
        token: str,
        *,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResult:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Action-Version": "1",
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        return self.http(
            "POST",
            f"/api/{module}/{action}",
            body=receipt_bytes(payload),
            headers=headers,
        )

    def psql_rows(self, query: str, timeout: float = 30) -> list[dict[str, Any]]:
        if ";" in query:
            raise ValueError("checker query is not read-only")
        wrapped = (
            "SELECT COALESCE(jsonb_agg(to_jsonb(q)), '[]'::jsonb)::text "
            f"FROM ({query}) AS q"
        )
        result = self.compose(
            (
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "course",
                "-At",
                "-c",
                wrapped,
            ),
            timeout=timeout,
        )
        if not result.ok:
            self.require_docker_result(
                result, "required read-only PostgreSQL query failed"
            )
        try:
            value = json.loads(result.stdout.strip())
        except json.JSONDecodeError as error:
            raise ContractError("PostgreSQL query did not return JSON rows") from error
        if not isinstance(value, list) or not all(
            isinstance(row, dict) for row in value
        ):
            raise ContractError("PostgreSQL query returned an invalid shape")
        return value

    def internal_http(
        self,
        service: str,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        script = (
            "import base64,json,sys,urllib.error,urllib.request;"
            "data=base64.b64decode(sys.argv[3]) if sys.argv[3] else None;"
            "req=urllib.request.Request(sys.argv[2],data=data,method=sys.argv[1],"
            "headers=json.loads(sys.argv[4]));"
            "status=0;out=b'';hdr={};"
            "\ntry:\n r=urllib.request.urlopen(req,timeout=15);status=r.status;"
            "out=r.read(1048576);hdr=dict(r.headers.items())"
            "\nexcept urllib.error.HTTPError as e:\n status=e.code;"
            "out=e.read(1048576);hdr=dict(e.headers.items())"
            "\nprint(json.dumps({'status':status,'data':base64.b64encode(out).decode(),"
            "'headers':hdr},separators=(',',':')))"
        )
        encoded = ""
        if body is not None:
            encoded = base64.b64encode(receipt_bytes(body)).decode("ascii")
        result = self.compose(
            (
                "exec",
                "-T",
                service,
                self.python_executables.get(service, "python3"),
                "-c",
                script,
                method,
                url,
                encoded,
                json.dumps(headers or {}, separators=(",", ":")),
            ),
            timeout=30,
        )
        self.require_docker_result(result, f"internal HTTP probe failed in {service}")
        try:
            value = json.loads(result.stdout.strip())
            return HttpResult(
                int(value["status"]),
                base64.b64decode(value["data"]),
                {str(key): str(item) for key, item in value["headers"].items()},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ContractError("internal HTTP probe returned invalid data") from error


class PublicChecker:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo = Path(args.repo).resolve()
        self.fixtures = Path(args.fixtures).resolve()
        self.output = Path(args.output).resolve()
        self.wrapper = Path(args.compose_wrapper).resolve()
        self.fixture: dict[str, Any] = {}
        self.checks: list[dict[str, Any]] = []
        self.cleanup_armed = False
        self.override = Path(tempfile.mkdtemp(prefix="week3-check-")) / "override.yaml"
        self.project = f"week3-public-{secrets.token_hex(4)}"
        self.gateway_port = self._free_port()
        self.jwt_secret = secrets.token_urlsafe(48)
        self.hmac_secret = secrets.token_urlsafe(40)
        self.capability = secrets.token_urlsafe(24)
        self.audit_token = secrets.token_urlsafe(24)
        self.issuer = "moduledev-course"
        self.audience = "moduledev-api"
        self.callback_base = ""
        self.receipt_token = issue_token(
            self.jwt_secret,
            "receipt-provider",
            "integration",
            ("receipt:write",),
            issuer=self.issuer,
            audience=self.audience,
        )
        self.client_token = issue_token(
            self.jwt_secret,
            "candidate-client",
            "web",
            ("payment:write", "payment:read", "workflow:read"),
            issuer=self.issuer,
            audience=self.audience,
        )
        self.reviewer_token = issue_token(
            self.jwt_secret,
            "reviewer",
            "backoffice",
            ("workflow:manual", "payment:read"),
            issuer=self.issuer,
            audience=self.audience,
        )
        environment = {
            "COMPOSE_PARALLEL_LIMIT": "2",
            "COURSE_GATEWAY_PORT": str(self.gateway_port),
            "COURSE_TEST_PROFILE": "1",
            "COURSE_JWT_ISSUER": self.issuer,
            "COURSE_JWT_AUDIENCE": self.audience,
            "COURSE_JWT_SIGNING_KEY": self.jwt_secret,
            "PROVIDER_CALLBACK_CAPABILITY": self.capability,
            "PROVIDER_CALLBACK_TOKEN": self.receipt_token,
            "PROVIDER_HMAC_SECRET": self.hmac_secret,
            "PROVIDER_AUDIT_TOKEN": self.audit_token,
        }
        self.sensitive = (
            self.jwt_secret,
            self.hmac_secret,
            self.capability,
            self.audit_token,
            self.receipt_token,
            self.client_token,
            self.reviewer_token,
        )
        self.harness: ComposeHarness | None = None
        self.environment = environment
        self.image_ids: dict[str, str] = {}
        self.forbidden_log_values: dict[str, str] = {
            "provider-callback-message": "Payment completed",
        }

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _record(self, name: str, phase: str, expected: Any, actual: Any) -> None:
        passed = expected(actual) if callable(expected) else actual == expected
        self.checks.append(
            {
                "name": name,
                "phase": phase,
                "status": "passed" if passed else "failed",
                "expected": _redact(
                    expected if not callable(expected) else "predicate", self.sensitive
                ),
                "actual": _redact(actual, self.sensitive),
            }
        )
        if not passed:
            raise ContractError(f"public check failed: {name}")

    def _find_compose(self) -> Path:
        for name in COMPOSE_NAMES:
            candidate = self.repo / name
            if candidate.is_file():
                return candidate
        raise ContractError("candidate Compose file is missing")

    def _write_override(self, config: dict[str, Any]) -> None:
        lines = ["services:"]
        for name in sorted(REQUIRED_SERVICES):
            lines.extend((f"  {name}:", "    container_name: !reset null"))
            if name == "gateway":
                lines.append(
                    '    ports: !override ["127.0.0.1:${COURSE_GATEWAY_PORT}:8080"]'
                )
            else:
                lines.append("    ports: !reset []")
        common_application = {
            "COURSE_JWT_ISSUER": "${COURSE_JWT_ISSUER}",
            "COURSE_JWT_AUDIENCE": "${COURSE_JWT_AUDIENCE}",
            "COURSE_JWT_SIGNING_KEY": "${COURSE_JWT_SIGNING_KEY}",
            "COURSE_TEST_PROFILE": "1",
        }
        environment: dict[str, dict[str, str]] = {
            "gateway": dict(common_application),
            "api": {
                **common_application,
                "PROVIDER_HMAC_SECRET": "${PROVIDER_HMAC_SECRET}",
            },
            "cli": dict(common_application),
            "worker-a": dict(common_application),
            "worker-b": dict(common_application),
            "outbox-dispatcher": {
                "PGAPPNAME": "week3-public-outbox-dispatcher",
                "PGUSER": "outbox_dispatcher",
                "PROVIDER_URL": "http://provider-simulator:8081",
                "OUTBOX_OWNER": "outbox-dispatcher",
                "COURSE_TEST_PROFILE": "1",
            },
            "receipt-adapter": {
                "PROVIDER_CALLBACK_CAPABILITY": "${PROVIDER_CALLBACK_CAPABILITY}",
                "PROVIDER_CALLBACK_TOKEN": "${PROVIDER_CALLBACK_TOKEN}",
                "PROVIDER_HMAC_SECRET": "${PROVIDER_HMAC_SECRET}",
                "RECEIPT_API_URL": "http://gateway:8080/api/receipt/accept",
                "COURSE_TEST_PROFILE": "1",
            },
            "inbox-reconciler": {
                "PGAPPNAME": "week3-public-inbox-reconciler",
                "PGUSER": "inbox_reconciler",
                "COURSE_TEST_PROFILE": "1",
            },
            "provider-simulator": {
                "CALLBACK_URL": self.callback_base + "/${PROVIDER_CALLBACK_CAPABILITY}",
                "SIMULATOR_MODE": "success",
                "CALLBACK_DELAY": "200ms",
                "CALLBACK_TIMEOUT": "1s",
                "CALLBACK_MAX_ATTEMPTS": "5",
                "CALLBACK_RETRY_DELAY": "200ms",
                "AUDIT_TOKEN": "${PROVIDER_AUDIT_TOKEN}",
            },
        }
        for service, values in environment.items():
            marker = lines.index(f"  {service}:") + 1
            insert = ["    environment:"] + [
                f"      {key}: {json.dumps(value)}" for key, value in values.items()
            ]
            lines[marker:marker] = insert
        volumes = config.get("volumes", {}) or {}
        if volumes:
            lines.append("volumes:")
            for index, name in enumerate(sorted(volumes), start=1):
                lines.extend(
                    (
                        f"  {name}:",
                        "    external: false",
                        f"    name: {self.project}-volume-{index}",
                    )
                )
        networks = config.get("networks", {}) or {}
        if networks:
            lines.append("networks:")
            for index, name in enumerate(sorted(networks), start=1):
                lines.extend(
                    (
                        f"  {name}:",
                        "    external: false",
                        f"    name: {self.project}-network-{index}",
                    )
                )
        self.override.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _payload(self, name: str) -> dict[str, Any]:
        relative = self.fixture["files"][name]
        value = json.loads((self.fixtures / relative).read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    @property
    def h(self) -> ComposeHarness:
        if self.harness is None:
            raise RuntimeError("Compose harness is not initialized")
        return self.harness

    def _poll_rows(
        self,
        query: str,
        predicate: Callable[[list[dict[str, Any]]], bool],
        *,
        timeout: float = 60,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        rows: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            rows = self.h.psql_rows(query)
            if predicate(rows):
                return rows
            time.sleep(0.2)
        raise ContractError("timed out waiting for candidate evidence")

    @staticmethod
    def _uuid(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ContractError(f"{field} is missing")
        try:
            return str(uuid.UUID(value))
        except ValueError as error:
            raise ContractError(f"{field} is not a UUID") from error

    @staticmethod
    def _text_id(value: Any, field: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or "\r" in value
            or "\n" in value
        ):
            raise ContractError(f"{field} is invalid")
        return value

    @staticmethod
    def _sql_text(value: str) -> str:
        encoded = value.encode("utf-8").hex()
        return f"convert_from(decode('{encoded}', 'hex'), 'UTF8')"

    def _create_and_submit(self, payload_name: str, label: str) -> tuple[str, str]:
        payload = self._payload(payload_name)
        self.forbidden_log_values[f"{label}-request-body"] = receipt_bytes(
            payload
        ).decode("utf-8")
        request = self.h.action(
            "payment",
            "request",
            payload,
            self.client_token,
            idempotency_key=f"public-request-{label}-{secrets.token_hex(4)}",
        )
        request_json = request.json()
        if (
            request.status != 200
            or request_json is None
            or request_json.get("status") != "ok"
        ):
            raise ContractError(f"payment.request failed for {label}")
        operation_id = self._uuid(
            request_json.get("result", {}).get("operationId"), "operationId"
        )
        submit = self.h.action(
            "payment",
            "submit",
            {"operationId": operation_id},
            self.client_token,
            idempotency_key=f"public-submit-{label}-{secrets.token_hex(4)}",
        )
        submit_json = submit.json()
        if (
            submit.status != 200
            or submit_json is None
            or submit_json.get("status") != "ok"
        ):
            raise ContractError(f"payment.submit failed for {label}")
        process_id = self._uuid(
            submit_json.get("result", {}).get("processId"), "processId"
        )
        return operation_id, process_id

    def _operation(self, operation_id: str) -> list[dict[str, Any]]:
        return self.h.psql_rows(
            "SELECT operation_id, operation_kind, status, process_id "
            "FROM autocheck.operations "
            f"WHERE operation_id = '{operation_id}'::uuid"
        )

    def _wait_operation(self, operation_id: str, status: str) -> list[dict[str, Any]]:
        return self._poll_rows(
            "SELECT operation_id, operation_kind, status, process_id "
            "FROM autocheck.operations "
            f"WHERE operation_id = '{operation_id}'::uuid",
            lambda rows: len(rows) == 1 and rows[0].get("status") == status,
        )

    def _external_request(self, operation_id: str) -> list[dict[str, Any]]:
        return self._poll_rows(
            "SELECT external_request_id, operation_id, state, payload_hash "
            "FROM autocheck.external_requests "
            f"WHERE operation_id = '{operation_id}'::uuid",
            lambda rows: len(rows) == 1,
        )

    def _provider_audit(self, external_request_id: str) -> HttpResult:
        quoted = urllib.parse.quote(external_request_id, safe="")
        return self.h.internal_http(
            "receipt-adapter",
            "GET",
            f"http://provider-simulator:8081/internal/audit/{quoted}",
            headers={"X-Audit-Token": self.audit_token},
        )

    def _check_admission_and_start(self) -> None:
        self.fixture = load_fixture(self.fixtures)
        compose_file = self._find_compose()
        if not self.wrapper.is_file():
            raise EnvironmentFailure("trusted Compose wrapper is missing")
        self.harness = ComposeHarness(
            repo=self.repo,
            compose_file=compose_file,
            override_file=self.override,
            wrapper=self.wrapper,
            project=self.project,
            gateway_port=self.gateway_port,
            environment=self.environment,
            sensitive=self.sensitive,
        )
        docker_probe = self.h.run(
            ("docker", "info", "--format", "{{.ServerVersion}}"), timeout=15
        )
        if not docker_probe.ok:
            raise EnvironmentFailure("Docker daemon is unavailable")
        config_result = self.h.candidate_config()
        if not config_result.ok:
            self.h.require_docker_result(
                config_result, "candidate Compose config is invalid"
            )
        try:
            config = json.loads(config_result.stdout)
        except json.JSONDecodeError as error:
            raise ContractError("candidate Compose config is not JSON") from error
        findings = _compose_contract_findings(config, self.repo)
        findings.extend(
            _secret_distribution_findings(
                config,
                {
                    "jwt-signing-key": (
                        self.jwt_secret,
                        {
                            (service, "COURSE_JWT_SIGNING_KEY")
                            for service in CSHARP_SERVICES + ("cli",)
                        },
                    ),
                    "provider-hmac-secret": (
                        self.hmac_secret,
                        {
                            ("api", "PROVIDER_HMAC_SECRET"),
                            ("receipt-adapter", "PROVIDER_HMAC_SECRET"),
                        },
                    ),
                    "callback-capability": (
                        self.capability,
                        {
                            ("receipt-adapter", "PROVIDER_CALLBACK_CAPABILITY"),
                            ("provider-simulator", "CALLBACK_URL"),
                        },
                    ),
                    "callback-token": (
                        self.receipt_token,
                        {("receipt-adapter", "PROVIDER_CALLBACK_TOKEN")},
                    ),
                    "provider-audit-token": (
                        self.audit_token,
                        {("provider-simulator", "AUDIT_TOKEN")},
                    ),
                },
            )
        )
        self._record("compose-contract", "admission", [], findings)
        self.callback_base = _provider_callback_base(config)
        self._write_override(config)
        self.cleanup_armed = True
        pull = self.h.run(("docker", "pull", PROVIDER_IMAGE), timeout=180)
        if not pull.ok:
            raise EnvironmentFailure("provider image pull failed")
        build = self.h.compose(("build", "--pull", "--no-cache"), timeout=1800)
        if not build.ok:
            self.h.require_docker_result(build, "candidate images did not build")
        services = config["services"]
        python_image = str(services[PYTHON_SERVICES[0]]["image"])
        built_python_id = self.h.local_image_id(python_image)
        up = self.h.compose(("up", "-d", "--no-build"), timeout=600)
        if not up.ok:
            self.h.require_docker_result(up, "candidate stack did not start")
        self.h.wait_gateway()
        for service in REQUIRED_SERVICES:
            self.h.container_id(service)
        python_ids = {self.h.image_id(service) for service in PYTHON_SERVICES}
        self._record("python-single-image", "admission", 1, len(python_ids))
        self._record(
            "python-locally-built-image",
            "admission",
            {built_python_id},
            python_ids,
        )
        for service in PYTHON_SERVICES:
            version = self.h.detect_python_runtime(service)
            self._record(
                f"python-version-{service}",
                "admission",
                lambda value: tuple(int(part) for part in value.split("."))
                >= (3, 12, 0),
                ".".join(str(part) for part in version),
            )
        for service in CSHARP_SERVICES:
            executable = Path(self.h.main_process_executable(service)).name.lower()
            self._record(
                f"csharp-runtime-{service}",
                "admission",
                "dotnet",
                executable,
            )
        self.image_ids = {
            service: self.h.image_id(service)
            for service in (*PYTHON_SERVICES, "api", "worker-a", "worker-b")
        }

    def _check_database_contract(self) -> None:
        columns = self.h.psql_rows(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'autocheck'"
        )
        names = {row.get("table_name") for row in columns}
        self._record("week3-stable-views", "startup", True, REQUIRED_VIEWS <= names)
        actual_columns: dict[str, set[Any]] = {}
        for row in columns:
            actual_columns.setdefault(str(row.get("table_name")), set()).add(
                row.get("column_name")
            )
        self._record(
            "week3-stable-view-columns",
            "startup",
            True,
            all(
                required <= actual_columns.get(view, set())
                for view, required in REQUIRED_VIEW_COLUMNS.items()
            ),
        )
        relation_kinds = self.h.psql_rows(
            "SELECT c.relname AS table_name, c.relkind "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'autocheck'"
        )
        required_relations = {
            row.get("table_name"): row.get("relkind")
            for row in relation_kinds
            if row.get("table_name") in REQUIRED_VIEWS
        }
        self._record(
            "week3-stable-relations-are-views",
            "startup",
            {view: "v" for view in REQUIRED_VIEWS},
            required_relations,
        )
        invalid_types = sorted(
            f"{row.get('table_name')}.{row.get('column_name')}:{row.get('data_type')}"
            for row in columns
            if row.get("table_name") in REQUIRED_VIEW_COLUMNS
            and row.get("column_name")
            in REQUIRED_VIEW_COLUMNS[str(row.get("table_name"))]
            and row.get("data_type")
            != _expected_column_type(str(row.get("column_name")))
        )
        self._record("week3-stable-view-types", "startup", [], invalid_types)
        contract_info = self.h.psql_rows(
            "SELECT contract_version, generated_at FROM autocheck.contract_info"
        )
        self._record(
            "week3-contract-info",
            "startup",
            True,
            len(contract_info) == 1
            and contract_info[0].get("contract_version") == "course-1"
            and contract_info[0].get("generated_at") is not None,
        )
        flows = self.h.psql_rows(
            "SELECT flow_name, flow_version, status, is_active "
            "FROM autocheck.flow_versions "
            "WHERE flow_name IN ('payment-processing', 'payment-review')"
        )
        active = {
            (row.get("flow_name"), row.get("flow_version"))
            for row in flows
            if row.get("status") == "PUBLISHED" and row.get("is_active") is True
        }
        self._record(
            "payment-flows-active",
            "startup",
            {("payment-processing", 1), ("payment-review", 1)},
            active,
        )
        roles = self.h.psql_rows(
            "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolreplication, "
            "rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname IN ('outbox_dispatcher', 'inbox_reconciler')"
        )
        elevated_flags = (
            "rolsuper",
            "rolcreaterole",
            "rolcreatedb",
            "rolreplication",
            "rolbypassrls",
        )
        self._record(
            "python-database-roles",
            "security",
            True,
            len(roles) == 2
            and all(not any(row.get(flag) for flag in elevated_flags) for row in roles),
        )
        memberships = self.h.psql_rows(
            "SELECT member.rolname AS member_role, granted.rolname AS granted_role "
            "FROM pg_catalog.pg_auth_members membership "
            "JOIN pg_catalog.pg_roles member ON member.oid = membership.member "
            "JOIN pg_catalog.pg_roles granted ON granted.oid = membership.roleid "
            "WHERE member.rolname IN ('outbox_dispatcher', 'inbox_reconciler')"
        )
        self._record("python-roles-no-memberships", "security", [], memberships)
        dml = self.h.psql_rows(
            "SELECT r.role_name, n.nspname AS schema_name, c.relname AS relation_name "
            "FROM (VALUES ('outbox_dispatcher'), ('inbox_reconciler')) AS r(role_name) "
            "CROSS JOIN pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%' "
            "AND (has_table_privilege(r.role_name, c.oid, 'SELECT') "
            "OR has_table_privilege(r.role_name, c.oid, 'INSERT') "
            "OR has_table_privilege(r.role_name, c.oid, 'UPDATE') "
            "OR has_table_privilege(r.role_name, c.oid, 'DELETE') "
            "OR has_table_privilege(r.role_name, c.oid, 'TRUNCATE') "
            "OR has_table_privilege(r.role_name, c.oid, 'REFERENCES') "
            "OR has_table_privilege(r.role_name, c.oid, 'TRIGGER'))"
        )
        self._record("python-roles-no-table-privileges", "security", [], dml)
        create_privileges = self.h.psql_rows(
            "SELECT r.role_name, n.nspname AS schema_name "
            "FROM (VALUES ('outbox_dispatcher'), ('inbox_reconciler')) AS r(role_name) "
            "CROSS JOIN pg_catalog.pg_namespace n "
            "WHERE n.nspname NOT LIKE 'pg_%' "
            "AND n.nspname <> 'information_schema' "
            "AND has_schema_privilege(r.role_name, n.oid, 'CREATE')"
        )
        self._record("python-roles-no-schema-create", "security", [], create_privileges)
        database_create = self.h.psql_rows(
            "SELECT r.role_name "
            "FROM (VALUES ('outbox_dispatcher'), ('inbox_reconciler')) AS r(role_name) "
            "WHERE has_database_privilege(r.role_name, current_database(), 'CREATE')"
        )
        self._record("python-roles-no-database-create", "security", [], database_create)
        functions = self.h.psql_rows(
            "SELECT p.proname AS function_name, "
            "pg_catalog.oidvectortypes(p.proargtypes) AS argument_types "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'delivery' "
            "AND p.proname IN ('claim_outbox', 'succeed_outbox', 'fail_outbox', "
            "'reconcile_inbox')"
        )
        signatures = {
            (row.get("function_name"), row.get("argument_types")) for row in functions
        }
        self._record(
            "python-fixed-sql-boundaries",
            "startup",
            {
                ("claim_outbox", "text, integer"),
                ("succeed_outbox", "uuid, text, bigint, text"),
                ("fail_outbox", "uuid, text, bigint, text"),
                ("reconcile_inbox", "integer"),
            },
            signatures,
        )
        function_privileges = self.h.psql_rows(
            "SELECT r.role_name, n.nspname AS schema_name, "
            "p.proname AS function_name, "
            "pg_catalog.oidvectortypes(p.proargtypes) AS argument_types "
            "FROM (VALUES ('outbox_dispatcher'), ('inbox_reconciler')) AS r(role_name) "
            "CROSS JOIN pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname NOT LIKE 'pg_%' "
            "AND n.nspname <> 'information_schema' "
            "AND has_function_privilege(r.role_name, p.oid, 'EXECUTE')"
        )
        actual_privileges = {
            (
                row.get("role_name"),
                row.get("schema_name"),
                row.get("function_name"),
                row.get("argument_types"),
            )
            for row in function_privileges
        }
        self._record(
            "python-fixed-function-privileges",
            "security",
            {
                ("outbox_dispatcher", "delivery", "claim_outbox", "text, integer"),
                (
                    "outbox_dispatcher",
                    "delivery",
                    "succeed_outbox",
                    "uuid, text, bigint, text",
                ),
                (
                    "outbox_dispatcher",
                    "delivery",
                    "fail_outbox",
                    "uuid, text, bigint, text",
                ),
                ("inbox_reconciler", "delivery", "reconcile_inbox", "integer"),
            },
            actual_privileges,
        )

    def _check_provider_path(self) -> tuple[str, str]:
        stop = self.h.compose(("stop", "outbox-dispatcher"), timeout=30)
        self.h.require_docker_result(stop, "cannot stop outbox-dispatcher")
        operation_id, process_id = self._create_and_submit(
            "providerRequest", "provider"
        )
        external_rows = self._external_request(operation_id)
        self._record("one-external-request", "outbox", 1, len(external_rows))
        external_id = self._text_id(
            external_rows[0].get("external_request_id"), "externalRequestId"
        )
        external_sql = self._sql_text(external_id)
        outbox = self.h.psql_rows(
            "SELECT outbox_id, external_request_id, state, attempt_count "
            "FROM autocheck.outbox "
            f"WHERE external_request_id = {external_sql}"
        )
        self._record(
            "dispatcher-stop-durable-outbox",
            "outbox",
            True,
            len(outbox) == 1 and outbox[0].get("state") in {"PENDING", "RETRY_WAIT"},
        )
        audit_before = self._provider_audit(external_id)
        self._record(
            "provider-not-called-before-dispatch", "outbox", 404, audit_before.status
        )
        missing_message_id = f"missing-{secrets.token_hex(6)}"
        message_id = f"invalid-{secrets.token_hex(6)}"
        invalid_receipt = {
            "externalRequestId": external_id,
            "messageId": message_id,
            "occurredAt": "2026-09-04T12:00:00Z",
            "outcome": "COMPLETED",
            "providerPaymentId": "invalid-provider",
            "version": 1,
        }
        missing_receipt = dict(invalid_receipt)
        missing_receipt["messageId"] = missing_message_id
        missing_receipt["providerPaymentId"] = missing_message_id
        missing = self.h.action(
            "receipt",
            "accept",
            missing_receipt,
            self.receipt_token,
            idempotency_key=missing_message_id,
        )
        missing_json = missing.json()
        self._record(
            "missing-signature-rejected",
            "receipt",
            True,
            missing.status == 403
            and missing_json is not None
            and missing_json.get("code") == "receipt.signature_required",
        )
        invalid = self.h.action(
            "receipt",
            "accept",
            invalid_receipt,
            self.receipt_token,
            idempotency_key=message_id,
            extra_headers={"X-Provider-Signature": "v1=" + "00" * 32},
        )
        invalid_json = invalid.json()
        self._record(
            "invalid-signature-rejected",
            "receipt",
            True,
            invalid.status == 401
            and invalid_json is not None
            and invalid_json.get("code") == "signature.invalid",
        )
        invalid_rows = self.h.psql_rows(
            "SELECT message_id FROM autocheck.inbox "
            f"WHERE message_id IN ({self._sql_text(missing_message_id)}, "
            f"{self._sql_text(message_id)})"
        )
        self._record("invalid-signatures-no-inbox", "receipt", [], invalid_rows)
        start = self.h.compose(
            ("up", "-d", "--no-build", "outbox-dispatcher"), timeout=30
        )
        self.h.require_docker_result(start, "cannot start outbox-dispatcher")
        completed = self._wait_operation(operation_id, "COMPLETED")
        self._record(
            "provider-success-completes-operation",
            "outbox",
            True,
            len(completed) == 1
            and completed[0].get("status") == "COMPLETED"
            and completed[0].get("process_id") == process_id,
        )
        stable_external = self.h.psql_rows(
            "SELECT external_request_id FROM autocheck.external_requests "
            f"WHERE operation_id = '{operation_id}'::uuid"
        )
        self._record(
            "one-external-request-after-completion", "outbox", 1, len(stable_external)
        )
        audit = self._provider_audit(external_id)
        audit_json = audit.json()
        self._record(
            "provider-single-payment",
            "outbox",
            True,
            audit.status == 200
            and audit_json is not None
            and audit_json.get("operationId") == external_id
            and audit_json.get("idempotencyKey") == external_id
            and audit_json.get("requestCount") == 1
            and audit_json.get("paymentCount") == 1,
        )
        evidence = self.h.psql_rows(
            "SELECT r.message_id, r.message_version, r.outcome, r.signature_valid, "
            "r.body_hash AS receipt_body_hash, i.body_hash AS inbox_body_hash, "
            "i.state AS inbox_state "
            "FROM autocheck.receipts r "
            "JOIN autocheck.inbox i ON i.message_id = r.message_id "
            f"WHERE r.external_request_id = {external_sql}"
        )
        self._record(
            "signed-receipt-evidence",
            "receipt",
            True,
            len(evidence) == 1
            and evidence[0].get("message_version") == 1
            and evidence[0].get("outcome") == "COMPLETED"
            and evidence[0].get("signature_valid") is True
            and bool(evidence[0].get("receipt_body_hash"))
            and evidence[0].get("receipt_body_hash")
            == evidence[0].get("inbox_body_hash")
            and evidence[0].get("inbox_state") == "APPLIED",
        )
        provider_payment_id = self._text_id(
            audit_json.get("providerPaymentId") if audit_json else None,
            "providerPaymentId",
        )
        receipt_message_id = self._text_id(
            evidence[0].get("message_id") if len(evidence) == 1 else None,
            "messageId",
        )
        self._record(
            "provider-receipt-identity",
            "receipt",
            provider_payment_id,
            receipt_message_id,
        )
        events = self.h.action(
            "operation",
            "events",
            {"operationId": operation_id},
            self.client_token,
            idempotency_key=f"public-events-{secrets.token_hex(5)}",
        )
        self._record("operation-events-action", "receipt", 200, events.status)
        return operation_id, process_id

    def _check_adapter_deduplication(self) -> tuple[str, str]:
        stopped = self.h.compose(("stop", "outbox-dispatcher"), timeout=30)
        self.h.require_docker_result(
            stopped, "cannot stop outbox-dispatcher for adapter scenario"
        )
        operation_id, process_id = self._create_and_submit("adapterRequest", "adapter")
        external_rows = self._external_request(operation_id)
        self._record("one-external-request-adapter", "receipt", 1, len(external_rows))
        external_id = self._text_id(
            external_rows[0].get("external_request_id"), "externalRequestId"
        )
        legacy = {
            "providerPaymentId": f"public-provider-{secrets.token_hex(5)}",
            "operationId": external_id,
            "result": "REJECTED",
            "message": "Public deterministic rejection",
            "occurredAt": "2026-09-04T12:00:00.123Z",
        }
        self.forbidden_log_values["adapter-callback-message"] = legacy["message"]
        self.forbidden_log_values["adapter-callback-body"] = receipt_bytes(
            legacy
        ).decode("utf-8")
        callback_url = self.callback_base + "/" + self.capability
        wrong_legacy = dict(legacy)
        wrong_message_id = f"wrong-capability-{secrets.token_hex(5)}"
        wrong_legacy["providerPaymentId"] = wrong_message_id
        wrong = self.h.internal_http(
            "receipt-adapter",
            "POST",
            self.callback_base + "/wrong",
            body=wrong_legacy,
            headers={"Content-Type": "application/json"},
        )
        self._record("wrong-capability-rejected", "receipt", 404, wrong.status)
        wrong_rows = self.h.psql_rows(
            "SELECT message_id FROM autocheck.inbox "
            f"WHERE message_id = {self._sql_text(wrong_message_id)}"
        )
        self._record("wrong-capability-no-inbox", "receipt", [], wrong_rows)
        first = self.h.internal_http(
            "receipt-adapter",
            "POST",
            callback_url,
            body=legacy,
            headers={"Content-Type": "application/json"},
        )
        duplicate = self.h.internal_http(
            "receipt-adapter",
            "POST",
            callback_url,
            body=legacy,
            headers={"Content-Type": "application/json"},
        )
        conflicting = dict(legacy)
        conflicting["result"] = "COMPLETED"
        conflicting["message"] = "Conflicting completion"
        self.forbidden_log_values["adapter-conflict-message"] = conflicting["message"]
        conflict = self.h.internal_http(
            "receipt-adapter",
            "POST",
            callback_url,
            body=conflicting,
            headers={"Content-Type": "application/json"},
        )
        first_json = first.json()
        duplicate_json = duplicate.json()
        self._record(
            "adapter-first-and-duplicate",
            "receipt",
            True,
            200 <= first.status < 300
            and duplicate.status == first.status
            and first_json is not None
            and duplicate_json == first_json,
        )
        conflict_json = conflict.json()
        self._record(
            "adapter-conflicting-callback",
            "receipt",
            True,
            conflict.status == 409
            and conflict_json is not None
            and conflict_json.get("code") == "idempotency.conflict",
        )
        rejected = self._wait_operation(operation_id, "REJECTED")
        self._record(
            "adapter-rejected-branch",
            "receipt",
            True,
            len(rejected) == 1
            and rejected[0].get("status") == "REJECTED"
            and rejected[0].get("process_id") == process_id,
        )
        message_id = legacy["providerPaymentId"]
        rows = self.h.psql_rows(
            "SELECT i.message_id, i.state, i.body_hash AS inbox_body_hash, "
            "r.outcome, r.external_request_id, r.message_version, "
            "r.signature_valid, r.body_hash AS receipt_body_hash "
            "FROM autocheck.inbox i JOIN autocheck.receipts r USING (message_id) "
            f"WHERE i.message_id = {self._sql_text(message_id)}"
        )
        expected_body_hash = hashlib.sha256(
            receipt_bytes(normalized_receipt(legacy))
        ).hexdigest()
        self._record(
            "adapter-one-inbox-receipt",
            "receipt",
            True,
            len(rows) == 1
            and rows[0].get("state") == "APPLIED"
            and rows[0].get("outcome") == "REJECTED",
        )
        self._record(
            "adapter-exact-signed-body",
            "receipt",
            True,
            len(rows) == 1
            and rows[0].get("message_version") == 1
            and rows[0].get("signature_valid") is True
            and rows[0].get("receipt_body_hash") == expected_body_hash
            and rows[0].get("inbox_body_hash") == expected_body_hash,
        )
        started = self.h.compose(
            ("up", "-d", "--no-build", "outbox-dispatcher"), timeout=30
        )
        self.h.require_docker_result(
            started, "cannot restart outbox-dispatcher after adapter scenario"
        )
        sentinel_operation, _ = self._create_and_submit(
            "providerRequest", "dispatcher-sentinel"
        )
        self._wait_operation(sentinel_operation, "COMPLETED")
        sentinel_external = self._external_request(sentinel_operation)
        sentinel_id = self._text_id(
            sentinel_external[0].get("external_request_id"), "externalRequestId"
        )
        sentinel_audit = self._provider_audit(sentinel_id)
        self._record(
            "dispatcher-resumed-after-confirmation",
            "receipt",
            200,
            sentinel_audit.status,
        )
        audit = self._provider_audit(external_id)
        self._record("confirmed-outbox-not-redelivered", "receipt", 404, audit.status)
        return operation_id, process_id

    def _check_review(self) -> tuple[str, str]:
        auto_operation, auto_process = self._create_and_submit(
            "reviewAutoRequest", "review-auto"
        )
        auto = self._wait_operation(auto_operation, "COMPLETED")
        decision = self.h.psql_rows(
            "SELECT source, outcome, rule_version FROM autocheck.decisions "
            f"WHERE process_id = '{auto_process}'::uuid"
        )
        self._record(
            "review-limit-rule",
            "review",
            True,
            len(auto) == 1
            and auto[0].get("status") == "COMPLETED"
            and len(decision) == 1
            and decision[0].get("source") == "LIMIT_RULE"
            and decision[0].get("rule_version") == "course-limit-v1"
            and decision[0].get("outcome") == "APPROVED",
        )
        manual_operation, manual_process = self._create_and_submit(
            "reviewManualRequest", "review-manual"
        )
        process_rows = self._poll_rows(
            "SELECT process_id, state, current_step_key FROM autocheck.processes "
            f"WHERE process_id = '{manual_process}'::uuid",
            lambda rows: len(rows) == 1 and rows[0].get("state") == "WAITING_MANUAL",
        )
        self._record(
            "review-waits-manual",
            "review",
            True,
            len(process_rows) == 1 and process_rows[0].get("state") == "WAITING_MANUAL",
        )
        steps = self.h.psql_rows(
            "SELECT step_instance_id, step_type, state FROM autocheck.steps "
            f"WHERE process_id = '{manual_process}'::uuid "
            "AND step_type = 'MANUAL' AND state = 'WAITING'"
        )
        if len(steps) != 1:
            raise ContractError("manual step projection is invalid")
        step_id = self._uuid(steps[0].get("step_instance_id"), "stepInstanceId")
        payload = {
            "processId": manual_process,
            "stepInstanceId": step_id,
            "decision": "APPROVED",
            "reason": "Public checker approval",
        }
        self.forbidden_log_values["manual-reason"] = payload["reason"]
        key = f"public-manual-{secrets.token_hex(5)}"
        accepted = self.h.action(
            "workflow",
            "manual",
            payload,
            self.reviewer_token,
            idempotency_key=key,
        )
        repeated = self.h.action(
            "workflow",
            "manual",
            payload,
            self.reviewer_token,
            idempotency_key=key,
        )
        changed = dict(payload)
        changed["decision"] = "REJECTED"
        conflict = self.h.action(
            "workflow",
            "manual",
            changed,
            self.reviewer_token,
            idempotency_key=key,
        )
        accepted_json = accepted.json()
        repeated_json = repeated.json()
        self._record(
            "manual-identical-repeat",
            "review",
            True,
            accepted.status == 200
            and repeated.status == 200
            and accepted_json is not None
            and repeated_json == accepted_json,
        )
        self._record("manual-changed-repeat-conflict", "review", 409, conflict.status)
        completed = self._wait_operation(manual_operation, "COMPLETED")
        decisions = self.h.psql_rows(
            "SELECT source, principal, outcome, reason_hash FROM autocheck.decisions "
            f"WHERE process_id = '{manual_process}'::uuid"
        )
        self._record(
            "manual-audited-decision",
            "review",
            True,
            len(completed) == 1
            and completed[0].get("status") == "COMPLETED"
            and len(decisions) == 1
            and decisions[0].get("source") == "MANUAL"
            and decisions[0].get("principal") == "reviewer"
            and decisions[0].get("outcome") == "APPROVED"
            and bool(decisions[0].get("reason_hash")),
        )
        return manual_operation, manual_process

    def _check_recreate_and_hygiene(self, operation_ids: Sequence[str]) -> None:
        for service in ("inbox-reconciler", "outbox-dispatcher"):
            stopped = self.h.compose(("stop", service), timeout=30)
            self.h.require_docker_result(
                stopped, f"cannot stop {service} for recovery scenario"
            )
        recovery_operation, _ = self._create_and_submit(
            "adapterRequest", "inbox-recovery"
        )
        recovery_external = self._external_request(recovery_operation)
        recovery_external_id = self._text_id(
            recovery_external[0].get("external_request_id"), "externalRequestId"
        )
        recovery_message_id = f"recovery-{secrets.token_hex(6)}"
        recovery_legacy = {
            "providerPaymentId": recovery_message_id,
            "operationId": recovery_external_id,
            "result": "REJECTED",
            "message": "Recovery callback",
            "occurredAt": "2026-09-04T12:00:00.456Z",
        }
        self.forbidden_log_values["recovery-callback-message"] = recovery_legacy[
            "message"
        ]
        accepted = self.h.internal_http(
            "receipt-adapter",
            "POST",
            self.callback_base + "/" + self.capability,
            body=recovery_legacy,
            headers={"Content-Type": "application/json"},
        )
        self._record(
            "recovery-receipt-accepted",
            "recovery",
            True,
            200 <= accepted.status < 300,
        )
        received = self._poll_rows(
            "SELECT state FROM autocheck.inbox "
            f"WHERE message_id = {self._sql_text(recovery_message_id)}",
            lambda rows: len(rows) == 1 and rows[0].get("state") == "RECEIVED",
        )
        self._record(
            "recovery-inbox-pending-before-recreate",
            "recovery",
            True,
            len(received) == 1 and received[0].get("state") == "RECEIVED",
        )
        result = self.h.compose(
            (
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                *PYTHON_SERVICES,
            ),
            timeout=60,
        )
        self.h.require_docker_result(result, "Python services did not recreate")
        after = {service: self.h.image_id(service) for service in PYTHON_SERVICES}
        self._record(
            "python-image-stable-after-recreate",
            "recovery",
            {service: self.image_ids[service] for service in PYTHON_SERVICES},
            after,
        )
        applied = self._poll_rows(
            "SELECT state FROM autocheck.inbox "
            f"WHERE message_id = {self._sql_text(recovery_message_id)}",
            lambda rows: len(rows) == 1 and rows[0].get("state") == "APPLIED",
        )
        recovered_operation = self._wait_operation(recovery_operation, "REJECTED")
        self._record(
            "reconciler-continues-after-recreate",
            "recovery",
            True,
            len(applied) == 1
            and applied[0].get("state") == "APPLIED"
            and len(recovered_operation) == 1
            and recovered_operation[0].get("status") == "REJECTED",
        )
        for operation_id in (*operation_ids, recovery_operation):
            rows = self._operation(operation_id)
            self._record(
                f"operation-persists-{operation_id[:8]}",
                "recovery",
                1,
                len(rows),
            )
        logs = self.h.compose(("logs", "--no-color"), timeout=30)
        self.h.require_docker_result(logs, "cannot inspect candidate logs")
        text = logs.stdout + "\n" + logs.stderr
        leaked = [secret for secret in self.sensitive if secret and secret in text]
        self._record("synthetic-secrets-not-in-logs", "security", [], leaked)
        leaked_messages = sorted(
            label
            for label, value in self.forbidden_log_values.items()
            if value
            and (
                value in text
                or base64.b64encode(value.encode("utf-8")).decode("ascii") in text
            )
        )
        self._record("full-messages-not-in-logs", "security", [], leaked_messages)
        csharp_after = {
            service: self.h.image_id(service)
            for service in ("api", "worker-a", "worker-b")
        }
        self._record(
            "csharp-images-not-recreated-with-python",
            "security",
            {service: self.image_ids[service] for service in csharp_after},
            csharp_after,
        )

    def execute(self) -> None:
        self._check_admission_and_start()
        self._check_database_contract()
        provider_operation, _ = self._check_provider_path()
        adapter_operation, _ = self._check_adapter_deduplication()
        manual_operation, _ = self._check_review()
        self._check_recreate_and_hygiene(
            (provider_operation, adapter_operation, manual_operation)
        )

    def cleanup(self) -> None:
        if not self.cleanup_armed or self.args.keep_stack or self.harness is None:
            return
        result = self.harness.compose(
            ("down", "--volumes", "--remove-orphans", "--rmi", "local"),
            timeout=600,
        )
        if not result.ok:
            raise EnvironmentFailure("isolated Docker cleanup failed")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--compose-wrapper", required=True)
    parser.add_argument("--keep-stack", action="store_true")
    return parser.parse_args(argv)


def _validated_report_path(path: Path) -> Path:
    parent = path.parent.resolve()
    if not parent.is_dir():
        raise EnvironmentFailure("report parent directory does not exist")
    if path.is_symlink():
        raise EnvironmentFailure("report path must not be a symlink")
    try:
        path.resolve().relative_to(parent)
    except ValueError as error:
        raise EnvironmentFailure("report path escapes its parent") from error
    return path


def _write_report(path: Path, report: dict[str, Any]) -> None:
    target = _validated_report_path(path)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = utc_now()
    checker: PublicChecker | None = None
    status = "error"
    exit_code = 2
    checks: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    message = ""
    try:
        checker = PublicChecker(args)
        checker.execute()
        status = "passed"
        exit_code = 0
    except (FixtureError, EnvironmentFailure) as error:
        status = "error"
        exit_code = 2
        message = str(error)
    except ContractError as error:
        status = "failed"
        exit_code = 1
        message = str(error)
    except Exception as error:  # Trusted checker bug, not a candidate failure.
        status = "error"
        exit_code = 2
        message = f"unexpected checker error: {type(error).__name__}"
    if checker is not None:
        checks = checker.checks
        commands = checker.harness.commands if checker.harness else []
        try:
            checker.cleanup()
        except Exception:
            if status == "passed":
                status = "error"
                exit_code = 2
                message = "trusted cleanup failed"
    if message:
        checks.append(
            {
                "name": "run-summary",
                "phase": "checker",
                "status": "failed" if status == "failed" else "error",
                "expected": "public checker completes",
                "actual": _redact(message, checker.sensitive if checker else ()),
            }
        )
    report = build_report(
        started_at=started_at,
        finished_at=utc_now(),
        status=status,
        checks=checks,
        commands=commands,
    )
    if report_has_forbidden_keys(report):
        report = build_report(
            started_at=started_at,
            finished_at=utc_now(),
            status="error",
            checks=[
                {
                    "name": "report-safety",
                    "phase": "checker",
                    "status": "error",
                    "expected": "safe report keys",
                    "actual": "forbidden report key detected",
                }
            ],
            commands=[],
        )
        exit_code = 2
    try:
        _write_report(Path(args.output), report)
    except Exception as error:
        print(f"Cannot write public report: {error}", file=sys.stderr)
        return 2
    print(f"week-3 public check: {status}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
