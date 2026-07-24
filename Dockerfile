# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig*.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM nginx:1.29-alpine AS frontend
ENV NGINX_ENVSUBST_FILTER=MIRDAIN_ALLOWED_HOSTS
COPY docker/nginx.conf /etc/nginx/templates/default.conf.template
COPY docker/security-headers.conf /etc/nginx/mirdain-security-headers.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

FROM python:3.13-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY backend ./backend
RUN python -m pip install --no-cache-dir .
RUN mkdir -p /app/backend/data && chown -R 10001:10001 /app/backend/data
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2)" || exit 1
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips=*"]
