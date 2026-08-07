from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from uuid import UUID

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec

ROOT = Path(__file__).resolve().parents[1]


def validate_openapi() -> None:
    for path in sorted((ROOT / "shared" / "openapi").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        validate_spec(document)


def validate_schemas() -> None:
    schema_paths = list((ROOT / "shared" / "schemas").glob("*.schema.json"))
    schema_paths.append(
        ROOT / "shared" / "schemas" / "power-monitor-rate-plan-1.0.json"
    )
    schema_paths.append(ROOT / "shared" / "schemas" / "sce-bill-extraction-1.0.json")
    for path in sorted(schema_paths):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    examples = json.loads(
        (ROOT / "shared" / "schemas" / "protocol-examples.json").read_text()
    )
    for name, schema_file in {
        "heartbeat": "heartbeat.schema.json",
        "reading_batch": "reading-batch.schema.json",
        "problem": "problem.schema.json",
        "health": "device-health.schema.json",
        "config": "device-config.schema.json",
    }.items():
        schema = json.loads((ROOT / "shared" / "schemas" / schema_file).read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            examples[name]
        )
    heartbeat_schema = json.loads(
        (ROOT / "shared" / "schemas" / "heartbeat.schema.json").read_text()
    )
    heartbeat_validator = Draft202012Validator(
        heartbeat_schema,
        format_checker=FormatChecker(),
    )
    exact_heartbeat = json.loads(
        (ROOT / "shared" / "fixtures" / "valid-heartbeat.json").read_text(
            encoding="utf-8"
        )
    )
    heartbeat_validator.validate(exact_heartbeat)
    for details in examples.get("storage_integrity_status_examples", {}).values():
        heartbeat_validator.validate(
            {
                **examples["heartbeat"],
                "sd": {
                    **examples["heartbeat"]["sd"],
                    "details": details,
                },
            }
        )
    heartbeat_validator.validate(
        {
            **examples["heartbeat"],
            "resources": {
                **examples["heartbeat"]["resources"],
                "ota_recovery": examples["ota_recovery_evidence_example"],
            },
        }
    )

    sensor_root = ROOT.parent / "power-monitor-sensor"
    sensor_schema_path = sensor_root / "shared/schemas/heartbeat.schema.json"
    sensor_fixture_path = sensor_root / "shared/fixtures/valid-heartbeat.json"
    if sensor_schema_path.exists() and sensor_fixture_path.exists():
        sensor_schema = json.loads(sensor_schema_path.read_text(encoding="utf-8"))
        sensor_fixture = json.loads(sensor_fixture_path.read_text(encoding="utf-8"))
        if heartbeat_schema != sensor_schema:
            raise AssertionError("server and sensor heartbeat schemas diverged")
        if exact_heartbeat != sensor_fixture:
            raise AssertionError(
                "server and sensor exact-wire heartbeat fixtures diverged"
            )
    rate_schema = json.loads(
        (ROOT / "shared" / "schemas" / "power-monitor-rate-plan-1.0.json").read_text()
    )
    rate_example = json.loads(
        (ROOT / "shared" / "examples" / "custom-rate-plan.json").read_text()
    )
    Draft202012Validator(rate_schema, format_checker=FormatChecker()).validate(
        rate_example
    )
    context_schema_path = (
        ROOT / "shared" / "schemas" / "utility-account-rate-context-1.0.json"
    )
    context_schema = json.loads(context_schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(context_schema)
    context_example = json.loads(
        (ROOT / "shared" / "examples" / "utility-account-rate-context.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(context_schema, format_checker=FormatChecker()).validate(
        context_example
    )


def validate_bill_import_context_contract() -> None:
    schema = json.loads(
        (
            ROOT / "shared" / "schemas" / "utility-account-rate-context-1.0.json"
        ).read_text(encoding="utf-8")
    )
    openapi = yaml.safe_load(
        (ROOT / "shared" / "openapi" / "server-api.yaml").read_text(encoding="utf-8")
    )
    component = openapi["components"]["schemas"]["UtilityAccountRateContextView"]
    schema_required = set(schema["required"])
    openapi_required = set(component["required"])
    if schema_required != openapi_required:
        raise AssertionError(
            "UtilityAccountRateContext required fields diverge between JSON Schema and OpenAPI"
        )
    generated_version = component["properties"]["generated_client_schema_version"][
        "const"
    ]
    schema_version = schema["properties"]["schema_version"]["const"]
    if generated_version != schema_version:
        raise AssertionError(
            "UtilityAccountRateContext generated-client version diverges from JSON Schema"
        )


def validate_vectors() -> None:
    document = json.loads(
        (ROOT / "shared" / "auth-test-vectors" / "hmac-sha256-v1.json").read_text()
    )
    vector = document["vectors"][0]
    secret = bytes.fromhex(vector["secret"])
    salt = bytes(32)
    info = b"pm-device-to-server-v1"
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    derived = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    if derived.hex() != vector["derived_key_hex"]:
        raise AssertionError("HKDF vector mismatch")
    body_hash = hashlib.sha256(vector["body_utf8"].encode()).hexdigest()
    canonical = "\n".join(
        [
            "PM-HMAC-SHA256-V1",
            vector["method"],
            vector["canonical_target"],
            vector["timestamp"],
            vector["nonce"],
            body_hash,
        ]
    )
    signature = hmac.new(derived, canonical.encode(), hashlib.sha256).hexdigest()
    if canonical != vector["canonical_string"] or signature != vector["signature"]:
        raise AssertionError("canonical/signature vector mismatch")

    ota = json.loads(
        (ROOT / "shared" / "auth-test-vectors" / "ota-manifest-v2.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = ota["manifest_without_hmac"]
    canonical_device_id = str(UUID(ota["device_id"]))
    if ota["hkdf_salt_utf8"] != canonical_device_id:
        raise AssertionError("OTA HKDF salt is not a canonical UUID")
    extract = hmac.new(
        canonical_device_id.encode("utf-8"),
        bytes.fromhex(ota["secret_hex"]),
        hashlib.sha256,
    ).digest()
    ota_key = hmac.new(
        extract,
        ota["hkdf_info_utf8"].encode("utf-8") + b"\x01",
        hashlib.sha256,
    ).digest()
    if ota_key.hex() != ota["derived_key_hex"]:
        raise AssertionError("OTA HKDF vector mismatch")
    ota_canonical = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if ota_canonical != ota["canonical_json_utf8"]:
        raise AssertionError("OTA manifest canonical JSON mismatch")
    ota_signature = (
        base64.urlsafe_b64encode(
            hmac.new(ota_key, ota_canonical.encode("utf-8"), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    if ota_signature != ota["manifest_hmac_base64url"]:
        raise AssertionError("OTA manifest HMAC vector mismatch")
    signed_manifest = {**manifest, "manifest_hmac": ota_signature}
    ota_schema = json.loads(
        (ROOT / "shared" / "schemas" / "ota-manifest-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(ota_schema, format_checker=FormatChecker()).validate(
        signed_manifest
    )

    reset = json.loads(
        (
            ROOT / "shared" / "auth-test-vectors" / "data-reset-receipt-v1.json"
        ).read_text(encoding="utf-8")
    )
    reset_device_id = str(UUID(reset["device_id"]))
    if reset_device_id != reset["receipt_hkdf_salt_utf8"]:
        raise AssertionError(
            "data-reset receipt HKDF salt is not the canonical device UUID"
        )
    reset_secret = bytes.fromhex(reset["secret_hex"])
    reset_directional_prk = hmac.new(bytes(32), reset_secret, hashlib.sha256).digest()
    reset_directional_key = hmac.new(
        reset_directional_prk,
        reset["directional_hkdf_info_utf8"].encode("utf-8") + b"\x01",
        hashlib.sha256,
    ).digest()
    if reset_directional_key.hex() != reset["directional_key_hex"]:
        raise AssertionError("data-reset directional key vector mismatch")
    reset_receipt_prk = hmac.new(
        reset_device_id.encode("utf-8"), reset_directional_key, hashlib.sha256
    ).digest()
    reset_receipt_key = hmac.new(
        reset_receipt_prk,
        reset["receipt_hkdf_info_utf8"].encode("utf-8") + b"\x01",
        hashlib.sha256,
    ).digest()
    if reset_receipt_key.hex() != reset["receipt_key_hex"]:
        raise AssertionError("data-reset receipt key vector mismatch")
    reset_canonical = json.dumps(
        reset["receipt_without_digest"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if reset_canonical != reset["canonical_json_utf8"]:
        raise AssertionError("data-reset receipt canonical JSON mismatch")
    reset_digest = hmac.new(
        reset_receipt_key, reset_canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if reset_digest != reset["receipt_digest_hex"]:
        raise AssertionError("data-reset receipt HMAC mismatch")

    agent = json.loads(
        (ROOT / "shared" / "auth-test-vectors" / "pm-agent-hmac-v2.json").read_text(
            encoding="utf-8"
        )
    )
    agent_secret = bytes.fromhex(agent["secret_hex"])
    agent_extract = hmac.new(bytes(32), agent_secret, hashlib.sha256).digest()
    device_key = hmac.new(
        agent_extract,
        agent["device_to_server_hkdf_info_utf8"].encode() + b"\x01",
        hashlib.sha256,
    ).digest()
    server_key = hmac.new(
        agent_extract,
        agent["server_to_device_hkdf_info_utf8"].encode() + b"\x01",
        hashlib.sha256,
    ).digest()
    if device_key.hex() != agent["device_to_server_key_hex"]:
        raise AssertionError("pm-agent device-to-server HKDF vector mismatch")
    if server_key.hex() != agent["server_to_device_key_hex"]:
        raise AssertionError("pm-agent server-to-device HKDF vector mismatch")
    request = agent["request"]
    if (
        hashlib.sha256(request["body_utf8"].encode()).hexdigest()
        != request["body_sha256"]
    ):
        raise AssertionError("pm-agent request body digest mismatch")
    if (
        hmac.new(
            device_key, request["canonical_utf8"].encode(), hashlib.sha256
        ).hexdigest()
        != request["signature_hex"]
    ):
        raise AssertionError("pm-agent request signature mismatch")
    response = agent["response"]
    if (
        hashlib.sha256(response["body_utf8"].encode()).hexdigest()
        != response["body_sha256"]
    ):
        raise AssertionError("pm-agent response body digest mismatch")
    if (
        hmac.new(
            server_key, response["canonical_utf8"].encode(), hashlib.sha256
        ).hexdigest()
        != response["signature_hex"]
    ):
        raise AssertionError("pm-agent response signature mismatch")


if __name__ == "__main__":
    validate_openapi()
    validate_schemas()
    validate_bill_import_context_contract()
    validate_vectors()
    print(
        "OpenAPI documents, JSON Schemas, protocol examples, and HMAC vectors are valid"
    )
