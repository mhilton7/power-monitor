from __future__ import annotations

from contract_check import (
    validate_bill_import_context_contract,
    validate_openapi,
    validate_schemas,
    validate_vectors,
)

if __name__ == "__main__":
    validate_openapi()
    validate_schemas()
    validate_bill_import_context_contract()
    validate_vectors()
    print(
        "OpenAPI documents, JSON Schemas, bill-import context contracts, "
        "examples, and HMAC vectors are valid"
    )
