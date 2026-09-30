"""Guarantees this API never returns Django's HTML error page or a raw
traceback for an unexpected bug - always clean JSON, and always logged
server-side so the failure is actually visible somewhere.

DRF's default exception handler already does this for anything that's a
DRF/Django exception it recognizes (ValidationError, Http404, ...). It
returns None for anything else (a plain Python exception, e.g. a bug), and
lets Django's normal machinery handle it - which means an HTML 500 page in
DEBUG, or a bare unstyled 500 in production. Neither is an acceptable
response from a JSON API.
"""
import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    logger.exception("Unhandled exception in %s", context.get("view"), exc_info=exc)
    return Response({"error": "Internal server error."}, status=500)
