import re
from functools import wraps
from flask import request, abort, session, redirect, url_for
import bleach
from wtforms.validators import ValidationError
from datetime import datetime, timedelta
import redis
import secrets

def sanitize_input(text):
    """Sanitize input text to prevent XSS attacks"""
    if text is None:
        return None
    return bleach.clean(str(text), strip=True)

def validate_phone_number(phone):
    """Validate phone number format"""
    pattern = re.compile(r'^\+?1?\d{9,15}$')
    if not pattern.match(phone):
        raise ValidationError('Invalid phone number format')
    return phone

def validate_zipcode(zipcode):
    """Validate US zipcode format"""
    pattern = re.compile(r'^\d{5}(?:-\d{4})?$')
    if not pattern.match(zipcode):
        raise ValidationError('Invalid zipcode format')
    return zipcode

def validate_password_strength(password):
    """
    Validate password meets security requirements
    Returns tuple of (bool, str) indicating if valid and message
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number"
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        return False, "Password must contain at least one special character"
    return True, "Password is valid"

def check_csrf_token():
    """Verify CSRF token"""
    token = session.get('csrf_token')
    if not token or token != request.form.get('csrf_token'):
        abort(403)

def csrf_protection(f):
    """Decorator to require CSRF token for POST requests"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == "POST":
            check_csrf_token()
        return f(*args, **kwargs)
    return decorated_function

def sanitize_form_data(form_data):
    """Sanitize all form inputs"""
    sanitized = {}
    for key, value in form_data.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_input(value)
        else:
            sanitized[key] = value
    return sanitized

# Rate limiting helper functions
def get_remote_addr():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def rate_limit_by_ip(limit=None, period=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not limit or not period:
                return f(*args, **kwargs)

            ip = get_remote_addr()
            key = f'rate_limit:{ip}:{f.__name__}'
            try:
                redis_client = redis.from_url('redis://localhost:6379/0')
                current = int(redis_client.get(key) or 0)
                
                if current >= limit:
                    return 'Too many requests', 429
                
                pipe = redis_client.pipeline()
                pipe.incr(key)
                pipe.expire(key, period)
                pipe.execute()
            except Exception as e:
                print(f"Rate limiting error: {e}")

            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Session security helpers
def regenerate_session():
    """Regenerate session ID to prevent session fixation"""
    from flask import session
    import secrets
    
    old_session = dict(session)
    session.clear()
    session.update(old_session)
    session.modified = True
    session['_fresh'] = True
    session['csrf_token'] = secrets.token_hex(32)

def init_session():
    """Initialize secure session settings"""
    session.permanent = True
    session['_fresh'] = True
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)

class SecurityManager:
    def __init__(self, app=None):
        self.app = app
        self.redis_client = None
        if app:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.redis_client = redis.from_url(app.config.get('REDIS_URL', 'redis://localhost:6379/0'))
        
        @app.before_request
        def check_session_expiry():
            if 'user_id' in session:
                last_active = session.get('last_active')
                if last_active:
                    last_active = datetime.fromisoformat(last_active)
                    if datetime.now() - last_active > timedelta(hours=24):
                        session.clear()
                        return redirect(url_for('login'))
                session['last_active'] = datetime.now().isoformat()

    def require_login(self, f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function

    def track_failed_login(self, username, ip):
        key = f"failed_login:{username}:{ip}"
        try:
            attempts = self.redis_client.incr(key)
            self.redis_client.expire(key, 3600)
            return attempts
        except:
            return 0

    def check_brute_force(self, username, ip):
        key = f"failed_login:{username}:{ip}"
        try:
            attempts = int(self.redis_client.get(key) or 0)
            return attempts >= 5
        except:
            return False

    def clear_failed_attempts(self, username, ip):
        key = f"failed_login:{username}:{ip}"
        try:
            self.redis_client.delete(key)
        except:
            pass

    def generate_reset_token(self, user_id):
        token = secrets.token_urlsafe(32)
        key = f"reset_token:{token}"
        try:
            self.redis_client.setex(key, 3600, str(user_id))
            return token
        except:
            return None

    def verify_reset_token(self, token):
        key = f"reset_token:{token}"
        try:
            user_id = self.redis_client.get(key)
            if user_id:
                self.redis_client.delete(key)
                return int(user_id)
        except:
            pass
        return None