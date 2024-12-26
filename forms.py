import re
from functools import wraps
from flask import request, abort, session
import bleach
from wtforms.validators import ValidationError

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
    """Get remote address for rate limiting"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    return request.remote_addr

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