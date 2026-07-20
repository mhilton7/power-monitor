from __future__ import annotations

from contract_check import validate_openapi, validate_schemas, validate_vectors

if __name__ == "__main__":
    validate_openapi()
    validate_schemas()
    validate_vectors()
    print("OpenAPI documents, JSON Schemas, examples, and HMAC vectors are valid")
