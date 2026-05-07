#!/bin/sh
set -eu
cat > /usr/share/nginx/html/config.js <<JS
(function () {
  window.API_BASE = "${BACKEND_API_BASE}";
})();
JS
