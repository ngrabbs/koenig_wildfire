"""Closed contract errors, not scientific classifications."""
class ContractViolation(ValueError):
    def __init__(self, code, detail=""):
        self.code = code
        self.detail = str(detail)[:300]
        super().__init__(code + (": " + self.detail if self.detail else ""))

class ExecutionDisabled(PermissionError):
    pass
