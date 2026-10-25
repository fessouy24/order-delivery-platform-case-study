from celery import shared_task


@shared_task
def worker_probe():
    """Manually invoked, side-effect-free worker connectivity probe."""
    return {"status": "ok"}
