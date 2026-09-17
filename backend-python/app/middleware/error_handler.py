import logging

from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config.settings import settings

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.detail,
            "error": str(exc.detail)
        }
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors"""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "message": "Validation error",
            "error": str(exc.errors())
        }
    )


async def general_exception_handler(request: Request, exc: Exception):
    """Handle general (unhandled) exceptions.

    The full exception, including its traceback, is always logged
    server-side for debugging. It is intentionally NOT included as-is in
    the response body: str(exc) can leak internal details (stack trace
    text, file paths, library internals) to API clients. In development
    we still surface the exception message to speed up local debugging;
    in every other environment the client only ever gets a generic
    message.
    """
    logger.error(
        "Unhandled exception while processing %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "Internal server error",
            "error": str(exc) if settings.ENVIRONMENT == "development" else "An unexpected error occurred"
        }
    )