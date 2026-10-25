from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def handle(exc, context):
    if isinstance(exc, IntegrityError):
        return Response(
            {
                "detail": "The record conflicts with an existing value or database rule. Refresh and review your input."
            },
            status=409,
        )
    if isinstance(exc, DjangoValidationError):
        return Response({"detail": "Invalid field value."}, status=400)
    return exception_handler(exc, context)
