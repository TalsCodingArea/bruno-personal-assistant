"""Shared parsing for LangGraph human approval responses."""


def is_approved(response: object) -> bool:
    if response is True:
        return True
    return (
        isinstance(response, dict)
        and isinstance(response.get("action"), str)
        and response["action"].casefold() == "approve"
    )
