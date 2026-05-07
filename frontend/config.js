(function () {
  // Same-origin when the frontend is served by the FastAPI app on :8000.
  // When opened from a static server on :5500 or another port, call the API
  // on the same hostname and API port. This works from another laptop on LAN:
  // http://192.168.x.x:5500 -> http://192.168.x.x:8000.
  var current = window.API_BASE || '';
  if (current) return;

  var apiPort = window.API_PORT || '8000';
  var host = window.location.hostname || '127.0.0.1';
  var protocol = window.API_SCHEME || 'http:';

  if (window.location.port === apiPort) {
    window.API_BASE = '';
  } else {
    window.API_BASE = protocol + '//' + host + ':' + apiPort;
  }
})();
