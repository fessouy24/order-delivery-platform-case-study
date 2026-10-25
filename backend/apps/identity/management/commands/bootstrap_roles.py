from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.identity.permissions import ROLES


class Command(BaseCommand):
    help = "Create the seven operational roles without default users or passwords."

    def handle(self, *args, **options):
        for name in ROLES:
            Group.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS("Operational roles are ready."))
