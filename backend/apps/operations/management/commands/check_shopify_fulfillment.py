from django.core.management.base import BaseCommand, CommandError

from apps.operations.fulfillment_transport import permissions
from apps.operations.providers import IntegrationError, Shopify


class Command(BaseCommand):
    help = "Check Shopify store and fulfillment scopes without creating fulfillments."

    def handle(self, *args, **options):
        try:
            scopes = permissions(Shopify())
            self.stdout.write("PASS: Shopify fulfillment permissions granted: " + ", ".join(scopes))
            self.stdout.write("Read-only check. No orders changed.")
        except IntegrationError as exc:
            raise CommandError(str(exc)) from None
