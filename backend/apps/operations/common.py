import json
import re
import unicodedata
from datetime import date

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.calendar import WorkCalendar

from .models import AreaAlias, AuditEvent, Order, OrderEvent, WorkPolicy


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    text = text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))
    text = re.sub(r"[\u064b-\u065f\u0670ـ]", "", text)
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def area_for(text):
    alias = AreaAlias.objects.select_related("area").filter(normalized_text=normalize(text)).first()
    return alias.area if alias else None


def work_calendar():
    policy = WorkPolicy.objects.filter(name="default").first()
    if not policy:
        return WorkCalendar()
    try:
        return WorkCalendar(
            frozenset(policy.weekdays),
            frozenset(date.fromisoformat(value) for value in policy.holidays),
            frozenset(date.fromisoformat(value) for value in policy.extra_workdays),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("The company calendar configuration is invalid.") from exc


def json_values(value):
    return json.loads(json.dumps(value, default=str))


@transaction.atomic
def audit(actor, action, entity, before=None, after=None):
    actor = actor if actor and actor.is_authenticated else None
    sequence = None
    if entity._meta.model_name == "order":
        Order.objects.select_for_update().get(pk=entity.pk)
        sequence = (
            OrderEvent.objects.filter(order=entity).aggregate(value=Max("sequence"))["value"] or 0
        ) + 1
    event = AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity_type=entity._meta.label_lower,
        entity_id=entity.pk,
        before=json_values(before or {}),
        after=json_values(after or {}),
    )
    if entity._meta.model_name == "order":
        OrderEvent.objects.create(
            order=entity,
            sequence=sequence,
            actor=actor,
            action=action,
            before=event.before,
            after=event.after,
        )
    return event


def business_today():
    return timezone.localdate()
