FROM node:22-bookworm-slim AS frontend
WORKDIR /web
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends nginx supervisor gettext-base \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home app
WORKDIR /app
COPY backend/requirements.txt backend/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app backend/ /app/
COPY --from=frontend --chown=app:app /web/dist/ /srv/web/
COPY infra/render-nginx.conf.template /etc/opsdemo/nginx.conf.template
COPY infra/render-supervisor.conf /etc/opsdemo/supervisor.conf
COPY infra/render-start.sh /app/render-start.sh
RUN mkdir /app/staticfiles /app/media && chown app:app /app/staticfiles /app/media \
    && chmod 755 /app/render-start.sh
USER app
EXPOSE 10000
CMD ["/app/render-start.sh"]
