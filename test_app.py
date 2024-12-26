import os
import tempfile
import pytest
from app import app, init_db
from security import validate_password_strength, sanitize_input

@pytest.fixture
def client():
    db_fd, app.config['DATABASE'] = tempfile.mkstemp()
    app.config['TESTING'] = True

    with app.test_client() as client:
        with app.app_context():
            init_db()
        yield client

    os.close(db_fd)
    os.unlink(app.config['DATABASE'])

def test_empty_db(client):
    """Start with a blank database."""
    rv = client.get('/')
    assert rv.status_code == 302  # Redirects to login

def test_login_page(client):
    """Test login page loads correctly."""
    rv = client.get('/login')
    assert rv.status_code == 200
    assert b'Login' in rv.data

def test_register_page(client):
    """Test registration page loads correctly."""
    rv = client.get('/register')
    assert rv.status_code == 200
    assert b'Register' in rv.data

def test_invalid_login(client):
    """Test login with invalid credentials."""
    rv = client.post('/login', data={
        'username': 'nonexistent',
        'password': 'wrong'
    }, follow_redirects=True)
    assert b'Invalid username or password' in rv.data

def test_registration(client):
    """Test user registration."""
    rv = client.post('/register', data={
        'username': 'testuser',
        'password': 'TestPass123!',
        'phone': '+11234567890',
        'zipcode': '12345',
        'preferred_time': '08:00 AM',
        'temperature_sensitivity': 'Normal'
    }, follow_redirects=True)
    assert rv.status_code == 200

def test_password_validation():
    """Test password strength validation."""
    # Test weak password
    valid, _ = validate_password_strength('weak')
    assert not valid

    # Test strong password
    valid, _ = validate_password_strength('StrongPass123!')
    assert valid

def test_input_sanitization():
    """Test input sanitization."""
    # Test XSS prevention
    dirty_input = '<script>alert("xss")</script>Hello'
    clean_output = sanitize_input(dirty_input)
    assert '<script>' not in clean_output
    assert 'Hello' in clean_output

def test_weather_api(client):
    """Test weather API endpoint."""
    # First register and login
    client.post('/register', data={
        'username': 'weathertest',
        'password': 'TestPass123!',
        'phone': '+11234567890',
        'zipcode': '12345',
        'preferred_time': '08:00 AM',
        'temperature_sensitivity': 'Normal'
    })
    
    client.post('/login', data={
        'username': 'weathertest',
        'password': 'TestPass123!'
    })
    
    rv = client.get('/weather')
    assert rv.status_code == 200
    
def test_rate_limiting(client):
    """Test rate limiting."""
    # Make multiple rapid requests to trigger rate limit
    for _ in range(6):
        rv = client.post('/login', data={
            'username': 'test',
            'password': 'test'
        })
    assert rv.status_code == 429  # Too Many Requests

if __name__ == '__main__':
    pytest.main([__file__])