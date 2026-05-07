FROM nginx:1.27-alpine

ENV PORT=10000 \
    BACKEND_API_BASE=http://localhost:8000

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY index.html admin.html favicon.ico /usr/share/nginx/html/
COPY static /usr/share/nginx/html/static
COPY docker-entrypoint.d/30-write-config.sh /docker-entrypoint.d/30-write-config.sh
RUN chmod +x /docker-entrypoint.d/30-write-config.sh

EXPOSE 10000
