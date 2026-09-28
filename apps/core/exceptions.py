from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import exception_handler


def _flatten_first_message(detail):
    if isinstance(detail, ErrorDetail):
        return str(detail)
    if isinstance(detail, list) and detail:
        return _flatten_first_message(detail[0])
    if isinstance(detail, dict) and detail:
        return _flatten_first_message(next(iter(detail.values())))
    return "Request failed."


def bbms_exception_handler(exc, context):
    """
    Wraps DRF's default exception handling so every error response has the
    shape: {"error": {"code": "...", "message": "...", "details": {...}}}
    """
    if isinstance(exc, ProtectedError):
        # e.g. deleting a bus/route/trip that trips/bookings still reference
        # (on_delete=PROTECT) — a client-fixable conflict, not a 500.
        return Response(
            {
                "error": {
                    "code": "in_use",
                    "message": "This record is still in use by other records and cannot be deleted.",
                    "details": {},
                }
            },
            status=status.HTTP_409_CONFLICT,
        )

    response = exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    if isinstance(detail, dict) and set(detail.keys()) == {"error"}:
        return response

    message = detail if isinstance(detail, str) else _flatten_first_message(detail)
    details = detail if isinstance(detail, (dict, list)) else {}

    response.data = {
        "error": {
            "code": getattr(exc, "default_code", exc.__class__.__name__),
            "message": message,
            "details": details,
        }
    }
    return response
