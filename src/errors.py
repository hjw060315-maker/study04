"""화면에 그대로 보여 줄 수 있는 오류."""


class AppError(Exception):
    """사용자에게 보여 줄 오류. code는 PRD의 오류 코드(invalid_image, too_large, model_unavailable, parse_failed 등)."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
