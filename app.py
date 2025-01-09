from flask import Flask, request, render_template, redirect, url_for, session, g, jsonify
import os
import sqlite3
import requests
import logging
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from openai import OpenAI  # Updated import
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta  # Added timedelta
from geopy.geocoders import Nominatim
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from pytz import timezone
import re
import json

# Load environment variables
load_dotenv()

# Constants and OpenAI client setup
DATABASE = 'jacket_app.db'
OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)  # Initialize client once

# Add default location (e.g., Madison, WI)
DEFAULT_LAT = 43.0731
DEFAULT_LON = -89.4012
DEFAULT_ZIP = "53717"

# Exception classes
class WeatherAPIException(Exception):
    pass

# Add debug logging for API keys at startup
logging.info("[INIT] Checking environment variables:")
logging.info(f"[INIT] OpenAI API Key present: {bool(OPENAI_API_KEY)}")
logging.info(f"[INIT] Twilio credentials present: {bool(os.getenv('TWILIO_ACCOUNT_SID'))} / {bool(os.getenv('TWILIO_AUTH_TOKEN'))}")
logging.info(f"[INIT] Twilio phone number: {os.getenv('TWILIO_PHONE_NUMBER')}")

# Utility functions
def generate_jacket_recommendation(temperature_f, wind_speed, condition):
    """Generate a simple jacket recommendation."""
    logging.info(f"[OPENAI] Starting recommendation for {temperature_f}°F")
    
    if not OPENAI_API_KEY:
        logging.error("[OPENAI] No API key available")
        return get_fallback_recommendation(temperature_f)
    
    try:
        logging.info("[OPENAI] Initializing client")
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        prompt = (
            f"The temperature is {temperature_f}°F with {condition}. "
            f"Wind speed is {wind_speed} mph. Suggest a simple, clear jacket recommendation."
        )
        
        logging.info("[OPENAI] Sending request")
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Changed to a more reliable model
            messages=[
                {"role": "system", "content": "You are a weather assistant providing short and clear jacket advice."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=40,
            temperature=0.2
        )
        
        recommendation = response.choices[0].message.content.strip()
        logging.info(f"[OPENAI] Success: {recommendation}")
        return recommendation
    except Exception as e:
        logging.error(f"[OPENAI] Error: {str(e)}")
        logging.exception("[OPENAI] Full exception details:")
        return get_fallback_recommendation(temperature_f)

def get_fallback_recommendation(temperature_f):
    """Fallback recommendations when OpenAI is unavailable."""
    if temperature_f < 32:
        return "Wear a thick, warm jacket."
    elif temperature_f < 50:
        return "A medium jacket is fine."
    return "A light jacket will do."

def should_wear_jacket(weather_data):
    # Round values before passing to recommendation function
    temperature = round(weather_data['main']['temp'])
    wind_speed = round(weather_data['wind']['speed'])
    condition = weather_data['weather'][0]['main']
    return generate_jacket_recommendation(temperature, wind_speed, condition)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

def get_coordinates(zipcode):
    try:
        geolocator = Nominatim(user_agent="jacket-app")
        location = geolocator.geocode({"postalcode": zipcode, "country": "US"})
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception as e:
        logging.error(f"Error fetching coordinates: {e}")
        return None, None

def validate_phone(phone):
    """Validates a 10-digit US phone number."""
    # Remove any non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) == 10:
        logging.info(f"Valid phone number: {digits}")
        return True
    logging.error(f"Invalid phone number: {phone} (digits: {digits})")
    return False

def format_phone_number(phone):
    """Format phone number to E.164 format (+1XXXXXXXXXX)."""
    # Remove any non-digit characters first
    digits = ''.join(filter(str.isdigit, phone))
    
    # If number already includes +1, remove it for validation
    if digits.startswith('1') and len(digits) == 11:
        digits = digits[1:]
    
    if not validate_phone(digits):
        logging.error(f"Phone validation failed for: {phone}")
        raise ValueError("Invalid phone number. Please enter a 10-digit US phone number.")
    
    formatted = f'+1{digits}'
    logging.info(f"Formatted phone number: {formatted}")
    return formatted

def create_user(phone, password, zipcode, preferred_time, temperature_sensitivity):
    logging.info(f"[REGISTRATION] Creating user with phone: {phone}")
    
    if not phone or not password:
        logging.error("[REGISTRATION] Missing required fields")
        raise ValueError("Phone number and password are required")
    
    try:
        formatted_phone = format_phone_number(phone)
        logging.info(f"[REGISTRATION] Formatted phone: {formatted_phone}")
        
        db = get_db()
        existing_user = db.execute('SELECT * FROM users WHERE phone_number = ?', [formatted_phone]).fetchone()
        if existing_user:
            logging.error(f"[REGISTRATION] Phone number already registered: {formatted_phone}")
            raise ValueError("Phone number is already registered")
        
        hashed_password = generate_password_hash(password)
        db.execute('''
            INSERT INTO users (phone_number, password, zipcode, preferred_time, temperature_sensitivity) 
            VALUES (?, ?, ?, ?, ?)
        ''', [formatted_phone, hashed_password, zipcode, preferred_time, temperature_sensitivity])
        db.commit()
        
        # Verify user was created
        new_user = db.execute('SELECT * FROM users WHERE phone_number = ?', [formatted_phone]).fetchone()
        logging.info(f"[REGISTRATION] User created successfully: {dict(new_user)}")
        
    except Exception as e:
        logging.error(f"[REGISTRATION] Error creating user: {str(e)}")
        logging.exception("[REGISTRATION] Full exception details:")
        raise

def create_app():
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    app.secret_key = os.environ.get('SECRET_KEY', 'dev_key_change_in_production')
    return app

app = create_app()

@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            phone = request.form['phone']
            password = request.form['password']
            logging.info(f"[LOGIN] Attempt for phone: {phone}")
            
            formatted_phone = format_phone_number(phone)
            logging.info(f"[LOGIN] Formatted phone: {formatted_phone}")
            
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE phone_number = ?', [formatted_phone]).fetchone()
            
            if user:
                logging.info(f"[LOGIN] User found: {dict(user)}")
                if check_password_hash(user['password'], password):
                    logging.info(f"[LOGIN] Password valid for user {user['id']}")
                    session['user_id'] = user['id']
                    return redirect(url_for('dashboard'))
                else:
                    logging.error(f"[LOGIN] Invalid password for user {user['id']}")
            else:
                logging.error(f"[LOGIN] No user found with phone: {formatted_phone}")
            
            return "Invalid phone number or password"
            
        except Exception as e:
            logging.error(f"[LOGIN] Error during login: {str(e)}")
            logging.exception("[LOGIN] Full exception details:")
            return "Error during login"
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone = request.form['phone']
        password = request.form['password']
        zipcode = request.form['zipcode']
        preferred_time = request.form['preferred_time']
        temperature_sensitivity = request.form.get('temperature_sensitivity', 'Normal')
        
        if not phone or not password:
            return "Phone number and password are required"
        
        try:
            create_user(phone, password, zipcode, preferred_time, temperature_sensitivity)
            return redirect(url_for('login'))
        except ValueError as e:
            return str(e)
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
    preferred_time = user["preferred_time"]
    try:
        if "AM" in preferred_time or "PM" in preferred_time:
            formatted_time = datetime.strptime(preferred_time, "%I:%M %p").strftime("%I:%M %p")
        else:
            formatted_time = datetime.strptime(preferred_time, "%H:%M").strftime("%I:%M %p")
    except ValueError as e:
        logging.error(f"Error formatting time: {e}")
        formatted_time = preferred_time
    
    form_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": formatted_time,
        "latitude": user["latitude"],
        "longitude": user["longitude"]
    }
    return render_template('dashboard.html', form_data=form_data)

@app.route('/weather')
def get_current_weather():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    try:
        user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
        
        weather_data_f = get_weather(zipcode=user['zipcode'])
        weather_data_c = get_weather(zipcode=user['zipcode'], units='metric')
        
        # Extract the weather icon from OpenWeatherMap response
        icon_code = weather_data_f['weather'][0]['icon']
        icon_url = f"http://openweathermap.org/img/wn/{icon_code}@2x.png"

        return jsonify({
            'temperature_f': round(weather_data_f['main']['temp']),
            'temperature_c': round(weather_data_c['main']['temp']),
            'condition': weather_data_f['weather'][0]['main'],
            'wind_speed': round(weather_data_f['wind']['speed']),
            'humidity': weather_data_f['main']['humidity'],
            'jacket_recommendation': should_wear_jacket(weather_data_f),
            'icon_url': icon_url  # Add icon URL to response
        })
    except Exception as e:
        logging.error(f"Error in get_current_weather: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            # Log all received data
            form_data = request.form.to_dict()
            logging.info(f"Received profile update data: {form_data}")
            
            # Format and validate phone number
            phone = format_phone_number(form_data.get('phone', ''))
            
            # Format time
            preferred_time = form_data.get('preferred_time', '07:30 AM')
            formatted_time = datetime.strptime(preferred_time, "%I:%M %p").strftime("%H:%M")
            logging.info(f"Formatted time: {formatted_time}")
            
            # Prepare update parameters
            params = [
                form_data.get('weather_notification_temp'),
                form_data.get('weather_notification_condition'),
                form_data.get('zipcode'),
                phone,
                formatted_time,
                form_data.get('latitude'),
                form_data.get('longitude'),
                session['user_id']
            ]
            
            # Execute update
            db = get_db()
            db.execute('''
                UPDATE users 
                SET weather_notification_temp = ?,
                    weather_notification_condition = ?,
                    zipcode = ?,
                    phone_number = ?,
                    preferred_time = ?,
                    latitude = ?,
                    longitude = ?
                WHERE id = ?
            ''', params)
            db.commit()
            
            # Verify update
            updated = db.execute('SELECT * FROM users WHERE id = ?', 
                               [session['user_id']]).fetchone()
            logging.info(f"Updated user data: {dict(updated)}")
            
            return jsonify({'message': 'Profile updated successfully'})
            
        except ValueError as e:
            logging.error(f"Validation error: {str(e)}")
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logging.error(f"Profile update error: {str(e)}")
            return jsonify({'error': 'Failed to update profile'}), 500

    # Handle GET request
    try:
        user = get_db().execute('SELECT * FROM users WHERE id = ?', 
                              [session['user_id']]).fetchone()
        user_dict = dict(user)
        logging.info(f"Retrieved user data: {user_dict}")
        
        # Format time for display
        stored_time = user_dict.get("preferred_time", "07:30")
        try:
            if ":" in stored_time:
                if "AM" in stored_time.upper() or "PM" in stored_time.upper():
                    formatted_time = stored_time
                else:
                    formatted_time = datetime.strptime(stored_time, "%H:%M").strftime("%I:%M %p")
            else:
                formatted_time = "07:30 AM"
        except ValueError as e:
            logging.error(f"Time format error: {e}")
            formatted_time = "07:30 AM"
        
        form_data = {
            "zipcode": user_dict["zipcode"],
            "phone": user_dict["phone_number"],
            "preferred_time": formatted_time,
            "latitude": user_dict["latitude"],
            "longitude": user_dict["longitude"],
            "weather_notification_temp": user_dict.get("weather_notification_temp", 30),
            "weather_notification_condition": user_dict.get("weather_notification_condition", "Snow"),
            "temperature_sensitivity": user_dict.get("temperature_sensitivity", "Normal")
        }
        logging.info(f"Sending form data to template: {form_data}")
        return render_template('profile.html', form_data=form_data)
    except Exception as e:
        logging.error(f"Error loading profile: {str(e)}")
        return redirect(url_for('login'))

def get_weather(zipcode=None, latitude=None, longitude=None, units='imperial'):
    """Get weather data with fallback to default location."""
    try:
        if not OPENWEATHERMAP_API_KEY:
            raise ValueError("OpenWeatherMap API key is not set")

        # Use provided location or fall back to defaults
        if zipcode:
            url = f"http://api.openweathermap.org/data/2.5/weather?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units={units}"
        elif latitude and longitude:
            url = f"http://api.openweathermap.org/data/2.5/weather?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units={units}"
        else:
            logging.warning("No location provided, using default location")
            url = f"http://api.openweathermap.org/data/2.5/weather?zip={DEFAULT_ZIP},us&appid={OPENWEATHERMAP_API_KEY}&units={units}"

        logging.debug(f"Fetching weather data from: {url}")
        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Weather API request failed: {e}")
        raise WeatherAPIException("Unable to fetch weather data")
    except Exception as e:
        logging.error(f"Unexpected error in get_weather: {e}")
        raise WeatherAPIException(str(e))

def send_text_message(to_number, message_body):
    """Send SMS with enhanced error handling and logging."""
    logging.info(f"[SMS] Starting send process")
    
    # Check credentials
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
    
    if not all([account_sid, auth_token, twilio_number]):
        logging.error("[SMS] Missing Twilio credentials")
        return False
    
    try:
        # Format phone number
        formatted_number = format_phone_number(to_number)
        logging.info(f"[SMS] Sending to: {formatted_number}")
        
        # Initialize client
        client = Client(account_sid, auth_token)
        
        # Send message
        message = client.messages.create(
            body=message_body,
            from_=twilio_number,
            to=formatted_number
        )
        
        logging.info(f"[SMS] Success! SID: {message.sid}")
        return True
    except Exception as e:
        logging.error(f"[SMS] Error: {str(e)}")
        logging.exception("[SMS] Full exception details:")
        return False

@app.route('/test-sms')
def test_sms():
    try:
        logging.info("Test SMS endpoint triggered")
        # Use your actual test phone number here
        result = send_text_message('6087702909', 'Test message from Render deployment')
        if result:
            return "Test message sent successfully!"
        return "Failed to send test message", 500
    except Exception as e:
        logging.error(f"Test SMS error: {str(e)}")
        return f"Error: {str(e)}", 500

def generate_weather_message(user_data, weather_data):
    temp_f = round(weather_data['main']['temp'])
    temp_c = round((temp_f - 32) * 5.0 / 9.0)  # Convert to Celsius
    condition = weather_data['weather'][0]['main']
    recommendation = should_wear_jacket(weather_data)
    
    return (
        f"Good morning!\n"
        f"Current Weather: {temp_f}°F ({temp_c}°C)\n"
        f"Condition: {condition}\n"
        f"Recommendation: {recommendation}"
    )

@app.route('/send-test-message')
def send_test_message():
    if 'user_id' not in session:
        return "Please log in first.", 401

    try:
        user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
        weather_data = get_weather(zipcode=user['zipcode'])
        message = generate_weather_message(user, weather_data)
        
        if send_text_message(user['phone_number'], message):
            return "Message sent successfully!"
        return "Failed to send message.", 500
    except Exception as e:
        logging.error(f"Test message error: {e}")
        return f"Error: {str(e)}", 500

@app.route('/weekly_weather')
def get_weekly_weather():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    try:
        user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
        
        # Use user location or fallback to defaults
        lat = user['latitude'] if user['latitude'] else DEFAULT_LAT
        lon = user['longitude'] if user['longitude'] else DEFAULT_LON
        
        url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&units=imperial&appid={OPENWEATHERMAP_API_KEY}"
        logging.debug(f"Fetching weekly forecast for lat={lat}, lon={lon}")
        
        response = requests.get(url)
        response.raise_for_status()
        
        data = response.json()
        daily_data = {
            'daily': []
        }
        
        # Process forecast data
        by_day = {}
        for item in data['list']:
            date = datetime.fromtimestamp(item['dt']).date()
            if date not in by_day:
                by_day[date] = item
        
        daily_data['daily'] = list(by_day.values())
        return jsonify(daily_data)
    except Exception as e:
        logging.error(f"Error in weekly_weather: {e}")
        return jsonify({'error': 'Unable to fetch weekly forecast'}), 500

@app.route('/hourly_weather')
def get_hourly_weather():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    try:
        user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
        
        lat = user['latitude'] if user['latitude'] else DEFAULT_LAT
        lon = user['longitude'] if user['longitude'] else DEFAULT_LON
        
        url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&units=imperial&appid={OPENWEATHERMAP_API_KEY}"
        response = requests.get(url)
        response.raise_for_status()
        
        data = response.json()
        hourly_data = []
        
        # Process each hourly entry
        for item in data['list'][:12]:
            hourly_data.append({
                'dt': item['dt'],
                'temp': round(item['main']['temp']) if 'main' in item and 'temp' in item['main'] else None,
                'weather': item['weather'][0] if item.get('weather') else {'main': 'Unknown', 'icon': '01d'},
                'humidity': item['main'].get('humidity', 0),
                'wind_speed': round(item['wind'].get('speed', 0)) if 'wind' in item else 0
            })
        
        return jsonify({'hourly': hourly_data})
    except Exception as e:
        logging.error(f"Error in hourly_weather: {e}")
        return jsonify({'error': 'Unable to fetch hourly forecast'}), 500

def send_daily_weather_update():
    current_time = datetime.now(timezone('America/Chicago'))
    logging.info(f"[SCHEDULER] Daily update started at {current_time}")
    logging.info(f"[SCHEDULER] Process ID: {os.getpid()}")
    
    # Check environment variables
    env_vars = {
        "OPENAI_API_KEY": bool(OPENAI_API_KEY),
        "TWILIO_ACCOUNT_SID": bool(os.environ.get("TWILIO_ACCOUNT_SID")),
        "TWILIO_AUTH_TOKEN": bool(os.environ.get("TWILIO_AUTH_TOKEN")),
        "TWILIO_PHONE_NUMBER": bool(os.environ.get("TWILIO_PHONE_NUMBER")),
        "OPENWEATHERMAP_API_KEY": bool(OPENWEATHERMAP_API_KEY)
    }
    logging.info(f"[SCHEDULER] Environment variables status: {json.dumps(env_vars)}")
    
    with app.app_context():
        try:
            db = get_db()
            users = db.execute('SELECT * FROM users').fetchall()
            logging.info(f"[SCHEDULER] Found {len(users)} users")
            
            for user in users:
                try:
                    user_dict = dict(user)
                    logging.info(f"[SCHEDULER] Processing user {user_dict['id']}")
                    
                    # Get weather and check conditions
                    weather_data = get_weather(zipcode=user_dict['zipcode'])
                    temperature = round(weather_data['main']['temp'])
                    condition = weather_data['weather'][0]['main']
                    
                    should_send = (
                        temperature < user_dict['weather_notification_temp'] or 
                        condition == user_dict['weather_notification_condition']
                    )
                    
                    if should_send:
                        message = generate_weather_message(user_dict, weather_data)
                        logging.info(f"[SCHEDULER] Sending message to {user_dict['phone_number']}")
                        result = send_text_message(user_dict['phone_number'], message)
                        logging.info(f"[SCHEDULER] Message send result: {result}")
                except Exception as e:
                    logging.error(f"[SCHEDULER] User processing error: {str(e)}")
                    logging.exception("[SCHEDULER] Full exception details:")
                    continue
        except Exception as e:
            logging.error(f"[SCHEDULER] Critical error: {str(e)}")
            logging.exception("[SCHEDULER] Full exception details:")

@app.route('/test-openai')
def test_openai():
    """Test endpoint for OpenAI integration."""
    try:
        logging.info("[TEST] Starting OpenAI test")
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Say 'OpenAI test successful' if you can read this."}],
            max_tokens=10
        )
        
        result = response.choices[0].message.content
        logging.info(f"[TEST] OpenAI response: {result}")
        
        return jsonify({
            "status": "success",
            "response": result,
            "api_key_present": bool(OPENAI_API_KEY)
        })
    except Exception as e:
        logging.error(f"[TEST] OpenAI test failed: {str(e)}")
        logging.exception("[TEST] Full exception details:")
        return jsonify({
            "status": "error",
            "error": str(e),
            "api_key_present": bool(OPENAI_API_KEY)
        }), 500

@app.route('/logout')
def logout():
    """Handle user logout by clearing session data."""
    session.clear()
    return redirect(url_for('login'))

@app.route('/test-scheduler')
def test_scheduler():
    """Test endpoint to trigger the scheduler immediately."""
    try:
        logging.info("[TEST] Adding immediate test job")
        run_date = datetime.now() + timedelta(seconds=10)
        
        scheduler.add_job(
            func=send_daily_weather_update,
            trigger='date',
            run_date=run_date,
            id="test_scheduler_job",
            replace_existing=True
        )
        
        logging.info(f"[TEST] Job scheduled for: {run_date}")
        return jsonify({
            "status": "success",
            "message": "Test job scheduled",
            "scheduled_time": run_date.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        logging.error(f"[TEST] Scheduler test error: {str(e)}")
        logging.exception("[TEST] Full exception details:")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/test-all')
def test_all():
    """Test both OpenAI and SMS functionality."""
    results = {
        "openai": {"status": None, "error": None},
        "sms": {"status": None, "error": None},
        "env_vars": {
            "openai_key": bool(OPENAI_API_KEY),
            "twilio_sid": bool(os.getenv("TWILIO_ACCOUNT_SID")),
            "twilio_token": bool(os.getenv("TWILIO_AUTH_TOKEN")),
            "twilio_number": bool(os.getenv("TWILIO_PHONE_NUMBER"))
        }
    }
    
    # Test OpenAI
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Test"}],
            max_tokens=5
        )
        results["openai"]["status"] = "success"
    except Exception as e:
        results["openai"]["status"] = "error"
        results["openai"]["error"] = str(e)
    
    # Test SMS
    try:
        sms_result = send_text_message("+16087702909", "Test message")
        results["sms"]["status"] = "success" if sms_result else "error"
    except Exception as e:
        results["sms"]["status"] = "error"
        results["sms"]["error"] = str(e)
    
    return jsonify(results)

# Initialize scheduler
jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
}
scheduler = BackgroundScheduler(
    jobstores=jobstores, 
    timezone=timezone('America/Chicago'),
    logger=logging.getLogger('apscheduler')
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# Add job with enhanced logging
try:
    job = scheduler.add_job(
        func=send_daily_weather_update,
        trigger='cron',
        hour=7,
        minute=30,
        id='daily_weather_job',
        name='Daily Weather Update',
        replace_existing=True
    )
    logging.info(f"[SCHEDULER] Job scheduled successfully")
    logging.info(f"[SCHEDULER] Next run time: {job.next_run_time}")
except Exception as e:
    logging.error(f"[SCHEDULER] Error scheduling job: {str(e)}")
    logging.exception("[SCHEDULER] Full exception details:")

scheduler.start()

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))