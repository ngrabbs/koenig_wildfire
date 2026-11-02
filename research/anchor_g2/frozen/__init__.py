"""B2 implementation only. No automatic execution, discovery or native imports."""
__all__ = ["open_session", "serialize_result"]

def open_session():
    from .runtime import require_execution_policy
    require_execution_policy()
    from .api import ResearchSession
    return ResearchSession()

def serialize_result(result):
    from .schema import serialize_result as serialize
    return serialize(result)
