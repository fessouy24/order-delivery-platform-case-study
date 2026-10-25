import io
import uuid
import warnings

from django.core.files.base import ContentFile
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework.decorators import api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.identity.permissions import allowed, require_role

from .common import audit
from .models import Item, ProductionWork


@api_view(["GET", "POST"])
def item_photo(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    if request.method == "GET":
        production_photo = (
            allowed(request.user, "Production")
            and ProductionWork.objects.filter(item=item, active=True).exists()
        )
        if not production_photo and not allowed(
            request.user, "Warehouse", "Supply Chain", "Management", "Sales", "Finance"
        ):
            from .views import visible_orders

            if (
                not allowed(request.user, "Courier")
                or not visible_orders(request.user)
                .filter(lines__item=item, lines__active=True)
                .exists()
            ):
                raise PermissionDenied("This product is not assigned to you.")
        if not item.photo:
            return Response({"detail": "No product photo has been uploaded."}, status=404)
        return FileResponse(item.photo.open("rb"), content_type="image/jpeg")
    require_role(request.user, "Warehouse")
    upload = request.FILES.get("photo")
    if not upload or upload.size > 2 * 1024 * 1024:
        raise ValidationError("Choose a JPG, PNG or WebP photo up to 2 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                if (
                    image.format not in {"JPEG", "PNG", "WEBP"}
                    or image.width * image.height > 20_000_000
                ):
                    raise ValueError("Unsupported photo")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((960, 960))
                rgba = image.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                clean.save(output, "JPEG", quality=85)
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError("The product photo could not be read. Use JPG, PNG or WebP.") from exc
    item.photo.save(f"{uuid.uuid4().hex}.jpg", ContentFile(output.getvalue()))
    audit(request.user, "catalog.photo_uploaded", item)
    return Response({"photo_url": item.photo_url})
