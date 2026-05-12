
class ApiError(RuntimeError):
    """Raised when the underlying API returns a non-zero error code."""
    def __init__(self, code: int, msg: str = "API call failed"):
        super().__init__(f"{msg} (code={code})")
        self.code = code
