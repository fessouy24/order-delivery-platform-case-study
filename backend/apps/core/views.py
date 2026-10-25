import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from redis import Redis

logger = logging.getLogger(__name__)


@require_GET
def live(request):
    return JsonResponse({"status": "ok", "service": "opsdemo-api"})


@require_GET
def ready(request):
    """Expose no connection strings or exception contents to callers."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        with Redis.from_url(
            settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2
        ) as redis:
            redis.ping()
    except Exception:
        logger.warning("Readiness dependency check failed")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
