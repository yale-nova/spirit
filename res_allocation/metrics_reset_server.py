import http.server
import json
import threading
from urllib.parse import urlparse, parse_qs

class MetricsResetHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for metrics reset API"""

    def __init__(self, *args, monitor=None, **kwargs):
        # If monitor is None, check if it's available as a class attribute
        if monitor is None:
            monitor = getattr(self.__class__, 'monitor', None)

        self.monitor = monitor
        # Initialize the parent class without passing monitor
        # This is a workaround since BaseHTTPRequestHandler doesn't accept custom params
        super().__init__(*args, **kwargs)

    def do_GET(self):
        """Handle GET requests to check server status"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()

        response = {
            "status": "ok",
            "message": "Metrics reset API is running",
            "usage": "Send a POST request to /reset_metrics with app_id parameter"
        }
        self.wfile.write(json.dumps(response).encode())

    def do_POST(self):
        """Handle POST requests to reset metrics for an application"""
        parsed_url = urlparse(self.path)

        # Check if the path is correct
        if parsed_url.path != '/reset_metrics':
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "error", "message": "Endpoint not found"}
            self.wfile.write(json.dumps(response).encode())
            return

        # Get the content length to read request body
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length).decode('utf-8')

        try:
            # Try to parse JSON request body
            data = json.loads(post_data)
            app_id = data.get('app_id')
        except json.JSONDecodeError:
            # If not JSON, try to parse query parameters
            query_params = parse_qs(parsed_url.query)
            app_id = query_params.get('app_id', [None])[0]

        # Handle case when app_id is not provided
        if not app_id:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "error", "message": "Missing required parameter: app_id"}
            self.wfile.write(json.dumps(response).encode())
            return

        # Convert app_id to integer
        try:
            app_id = int(app_id)
        except ValueError:
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "error", "message": "app_id must be an integer"}
            self.wfile.write(json.dumps(response).encode())
            return

        # Reset metrics for the specified application
        if self.monitor and hasattr(self.monitor, 'reset_metrics_for_app'):
            success = self.monitor.reset_metrics_for_app(app_id)

            if success:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {"status": "success", "message": f"Metrics reset for application {app_id}"}
                self.wfile.write(json.dumps(response).encode())
            else:
                self.send_response(404)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {"status": "error", "message": f"Application {app_id} not found"}
                self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"status": "error", "message": "Monitor not available or reset_metrics_for_app method not implemented"}
            self.wfile.write(json.dumps(response).encode())

class MetricsResetServer:
    """Server class for metrics reset API"""

    def __init__(self, monitor, port=60000):
        """Initialize the server with the monitor instance and port"""
        self.monitor = monitor
        self.port = port
        self.server = None
        self.server_thread = None

    @staticmethod
    def _create_handler_class(monitor):
        """Create a handler class with access to the monitor instance"""
        return type('BoundMetricsResetHandler', (MetricsResetHandler,), {'monitor': monitor})

    def start(self):
        """Start the HTTP server in a separate thread"""
        handler = self._create_handler_class(self.monitor)
        self.server = http.server.HTTPServer(('0.0.0.0', self.port), handler)

        print(f"Starting metrics reset API server on port {self.port}")
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True  # Allow the thread to exit when the main thread exits
        self.server_thread.start()
        return self.server

    def stop(self):
        """Stop the HTTP server cleanly"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            print("Metrics reset API server stopped")
