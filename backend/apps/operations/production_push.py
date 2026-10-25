from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ProductionDevice, ProductionNotice, PushDelivery


def send_pending():
    if not settings.PRODUCTION_PUSH_ENABLED:
        return {"configured": False, "sent": 0}
    # Load company-owned credentials only on the server; never in an APK.
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        settings.FCM_CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
    )
    session = AuthorizedSession(credentials)
    sent = 0
    for device in ProductionDevice.objects.filter(
        user__is_active=True, user__groups__name="Production"
    ).distinct():
        for notice in ProductionNotice.objects.filter(
            work__active=True, created_at__gte=device.created_at
        ).order_by("created_at")[:100]:
            delivery, _ = PushDelivery.objects.get_or_create(notice=notice, device=device)
            with transaction.atomic():
                delivery = PushDelivery.objects.select_for_update().get(pk=delivery.pk)
                if (
                    delivery.sent_at
                    or delivery.next_attempt_at
                    and delivery.next_attempt_at > timezone.now()
                ):
                    continue
                response = session.post(
                    f"https://fcm.googleapis.com/v1/projects/{settings.FCM_PROJECT_ID}/messages:send",
                    json={
                        "message": {
                            "token": device.token,
                            "data": {
                                "notice_id": str(notice.pk),
                                "kind": notice.work.kind,
                                "message": notice.message,
                            },
                            "android": {"priority": "high", "ttl": "3600s"},
                        }
                    },
                    timeout=15,
                )
                delivery.attempts += 1
                if response.status_code == 200:
                    delivery.sent_at = timezone.now()
                    sent += 1
                elif response.status_code == 404 and "UNREGISTERED" in response.text:
                    device.delete()
                    break
                else:
                    delivery.next_attempt_at = timezone.now() + timedelta(
                        seconds=min(3600, 30 * 2 ** min(delivery.attempts, 7))
                    )
                delivery.save(update_fields=["sent_at", "attempts", "next_attempt_at"])
    return {"configured": True, "sent": sent}
