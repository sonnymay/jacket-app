import os
import time
import functools
import logging
from flask import request, g
from logging.handlers import RotatingFileHandler
from prometheus_client import Counter, Histogram, Info, Gauge
from datetime import datetime

# Ensure logs directory exists
if not os.path.exists('logs'):
    os.makedirs('logs')

# Configure logging for monitoring
logging.basicConfig(
    handlers=[
        RotatingFileHandler(
            'logs/monitoring.log',
            maxBytes=100000,
            backupCount=3
        )
    ],
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
)

# Prometheus metrics
REQUEST_COUNT = Counter(
    'flask_request_count',
    'App Request Count',
    ['method', 'endpoint', 'status']
)

REQUEST_LATENCY = Histogram(
    'flask_request_latency_seconds',
    'Request latency',
    ['endpoint']
)

ERROR_COUNT = Counter(
    'flask_error_count',
    'App Error Count',
    ['error_type']
)

ACTIVE_USERS = Gauge(
    'flask_active_users',
    'Number of active users'
)

API_REQUEST_COUNT = Counter(
    'flask_api_request_count',
    'External API Request Count',
    ['api_name']
)

APP_INFO = Info('flask_app_info', 'Application information')

# Initialize metrics
api_requests = {}
response_times = {}

def init_metrics(app):
    """Initialize metrics collection"""
    APP_INFO.info({
        'version': app.config.get('VERSION', 'unknown'),
        'environment': app.config.get('ENV', 'development'),
        'start_time': datetime.now().isoformat()
    })

    @app.before_request
    def before_request():
        g.start_time = time.time()

    @app.after_request
    def after_request(response):
        # Record request latency
        latency = time.time() - g.start_time
        REQUEST_LATENCY.labels(endpoint=request.endpoint).observe(latency)
        
        # Record request count
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.endpoint,
            status=response.status_code
        ).inc()
        
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time
            endpoint = request.endpoint or 'unknown'
            
            logging.info(
                f"Request to {endpoint} completed in {elapsed:.2f}s "
                f"with status {response.status_code}"
            )

            # Update metrics
            if endpoint in response_times:
                response_times[endpoint].append(elapsed)
            else:
                response_times[endpoint] = [elapsed]

        return response

    @app.errorhandler(Exception)
    def handle_error(error):
        error_type = error.__class__.__name__
        ERROR_COUNT.labels(error_type=error_type).inc()
        app.logger.error(f"Error occurred: {error_type} - {str(error)}")
        raise error

def track_api_request(api_name):
    """Decorator to track external API requests"""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            API_REQUEST_COUNT.labels(api_name=api_name).inc()
            start_time = time.time()
            
            try:
                result = f(*args, **kwargs)
                status = 'success'
            except Exception as e:
                status = 'error'
                logging.error(f"API error in {api_name}: {str(e)}")
                raise
            finally:
                elapsed = time.time() - start_time
                
                # Update metrics
                if api_name not in api_requests:
                    api_requests[api_name] = {'success': 0, 'error': 0, 'total_time': 0}
                
                api_requests[api_name][status] += 1
                api_requests[api_name]['total_time'] += elapsed
                
                logging.info(
                    f"API call to {api_name} completed in {elapsed:.2f}s "
                    f"with status {status}"
                )
            
            return result
        return wrapper
    return decorator

def update_active_users(delta=1):
    """Update the number of active users"""
    ACTIVE_USERS.inc(delta)

class RequestTracker:
    """Track detailed request information"""
    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger('request_tracker')
        
    def track_request(self):
        """Log detailed request information"""
        endpoint = request.endpoint
        method = request.method
        ip = request.remote_addr
        user_agent = request.headers.get('User-Agent')
        
        self.logger.info(
            f"Request - Endpoint: {endpoint}, Method: {method}, "
            f"IP: {ip}, User-Agent: {user_agent}"
        )

def setup_monitoring(app):
    """Set up all monitoring components"""
    # Initialize Prometheus metrics
    init_metrics(app)
    
    # Create request tracker
    tracker = RequestTracker(app)
    
    @app.before_request
    def track_request():
        tracker.track_request()
    
    @app.before_request
    def track_session():
        if 'user_id' in request.args:
            update_active_users(1)
    
    @app.teardown_request
    def teardown_request(exception=None):
        if exception:
            ERROR_COUNT.labels(error_type=str(type(exception).__name__)).inc()
    
    # Setup logging
    if not app.debug:
        if not os.path.exists('logs'):
            os.mkdir('logs')
        file_handler = RotatingFileHandler(
            'logs/jacket_app.log',
            maxBytes=10240,
            backupCount=10
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s '
            '[in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Jacket App startup')

# Utility functions for monitoring
def log_weather_request(location, success):
    """Log weather API requests"""
    API_REQUEST_COUNT.labels(api_name='weather_api').inc()
    if not success:
        ERROR_COUNT.labels(error_type='WeatherAPIError').inc()

def log_sms_request(success):
    """Log SMS API requests"""
    API_REQUEST_COUNT.labels(api_name='twilio_api').inc()
    if not success:
        ERROR_COUNT.labels(error_type='TwilioAPIError').inc()

def log_gpt_request(success):
    """Log OpenAI GPT API requests"""
    API_REQUEST_COUNT.labels(api_name='openai_api').inc()
    if not success:
        ERROR_COUNT.labels(error_type='OpenAIAPIError').inc()