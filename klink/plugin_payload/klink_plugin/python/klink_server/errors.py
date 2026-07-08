"""Error codes shared between server and client."""


class ErrorCode:
    BAD_REQUEST = "ERR_BAD_REQUEST"
    UNKNOWN_METHOD = "ERR_UNKNOWN_METHOD"
    BAD_PARAMS = "ERR_BAD_PARAMS"
    NO_VIEW = "ERR_NO_VIEW"
    NO_LAYOUT = "ERR_NO_LAYOUT"
    NO_SELECTION = "ERR_NO_SELECTION"
    NOT_FOUND = "ERR_NOT_FOUND"
    TXN_STATE = "ERR_TXN_STATE"
    CANCELLED = "ERR_CANCELLED"
    TIMEOUT = "ERR_TIMEOUT"
    EXEC = "ERR_EXEC"
    INTERNAL = "ERR_INTERNAL"


class RpcError(Exception):
    """
    Exception that method handlers raise to return a structured error
    to the client. `hint` is meant to be LLM/agent-friendly guidance.
    """

    def __init__(self, code: str, message: str, hint: str = "", data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.data = data

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.data is not None:
            d["data"] = self.data
        return d
