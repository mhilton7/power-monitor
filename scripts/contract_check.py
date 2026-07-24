from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

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
    generated = (
        ROOT / "frontend" / "src" / "generated" / "utilityAccountRateContext.ts"
    ).read_text(encoding="utf-8")
    runtime = (ROOT / "frontend" / "src" / "billImportContext.ts").read_text(
        encoding="utf-8"
    )
    for key in schema["required"]:
        if key not in generated:
            raise AssertionError(f"generated UtilityAccountRateContext omits {key}")
    version = schema["properties"]["schema_version"]["const"]
    if version not in generated or (
        version not in runtime
        and "UTILITY_ACCOUNT_RATE_CONTEXT_SCHEMA_VERSION" not in runtime
    ):
        raise AssertionError("frontend contract/runtime schema version is stale")
    if "parseUtilityAccountRateContext" not in runtime:
        raise AssertionError("frontend runtime validation is missing")


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


if __name__ == "__main__":
    validate_openapi()
    validate_schemas()
    validate_bill_import_context_contract()
    validate_vectors()
    print(
        "OpenAPI documents, JSON Schemas, protocol examples, and HMAC vectors are valid"
    )
