from app.core.exceptions import UnsupportedCapabilityError


def test_unsupported_capability_error_exposes_structured_details():
    error = UnsupportedCapabilityError(
        runtime_id="hermes",
        capability="tool_allow",
        operation="gene.allow_tools",
    )

    assert error.code == 40080
    assert error.status_code == 400
    assert error.message_key == "errors.runtime.unsupported_capability"
    assert error.message_params == {
        "runtime_id": "hermes",
        "capability": "tool_allow",
        "operation": "gene.allow_tools",
    }
    assert error.details == {
        "code": "UNSUPPORTED_CAPABILITY",
        "runtime_id": "hermes",
        "capability": "tool_allow",
        "operation": "gene.allow_tools",
    }
