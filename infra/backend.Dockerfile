FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home app && mkdir /app/staticfiles /app/media && chown app:app /app/staticfiles /app/media
COPY --chown=app:app backend/ /app/
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "--access-logfile", "-"]
