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
import pytz

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

# Add debug logging for environment variables at startup
logging.info("[ENV] Checking environment variables:")
logging.info(f"[ENV] TWILIO_ACCOUNT_SID: {'Present' if os.getenv('TWILIO_ACCOUNT_SID') else 'Missing'}")
logging.info(f"[ENV] TWILIO_AUTH_TOKEN: {'Present' if os.getenv('TWILIO_AUTH_TOKEN') else 'Missing'}")
logging.info(f"[ENV] TWILIO_PHONE_NUMBER: {os.getenv('TWILIO_PHONE_NUMBER')}")
logging.info(f"[ENV] OPENWEATHERMAP_API_KEY: {'Present' if OPENWEATHERMAP_API_KEY else 'Missing'}")
logging.info(f"[ENV] OPENAI_API_KEY: {'Present' if OPENAI_API_KEY else 'Missing'}")

# Utility functions
def generate_jacket_recommendation(temperature_f, wind_speed, condition):
    """Generate a short, friendly jacket recommendation."""
    logging.info(f"[OPENAI] Generating recommendation for {temperature_f}°F")
    
    if not OPENAI_API_KEY:
        logging.error("[OPENAI] No API key available")
        return get_fallback_recommendation(temperature_f)
    
    try:
        prompt = (
            f"Given {temperature_f}°F weather with {condition} conditions and {wind_speed} mph winds, "
            "provide a SHORT (max 15 words), complete jacket recommendation. Be direct and friendly."
        )
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful weather assistant. Keep responses under 15 words."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=30,
            temperature=0.7
        )
        recommendation = response.choices[0].message.content.strip()
        logging.info(f"[OPENAI] Success: {recommendation}")
        return recommendation
    except Exception as e:
        logging.error(f"[OPENAI] Error: {str(e)}")
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
    """Initialize the database and create tables"""
    try:
        db = get_db()
        # Define the schema directly here instead of reading from file
        schema = '''
        DROP TABLE IF EXISTS users;
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_number TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            zipcode TEXT,
            preferred_time TEXT DEFAULT '07:30',
            temperature_sensitivity TEXT DEFAULT 'Normal',
            latitude REAL,
            longitude REAL,
            weather_notification_temp INTEGER DEFAULT 32,
            weather_notification_condition TEXT DEFAULT 'Snow'
        );
        '''
        db.executescript(schema)
        db.commit()
        logging.info("[DB] Database initialized successfully")
    except Exception as e:
        logging.error(f"[DB] Error initializing database: {e}")
        raise

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
        
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
    
    if user is None:
        # If user not found, clear session and redirect to login
        session.clear()
        return redirect(url_for('login'))
    
    try:
        preferred_time = user["preferred_time"] or "07:30"  # Default if None
        
        if "AM" in preferred_time.upper() or "PM" in preferred_time.upper():
            formatted_time = datetime.strptime(preferred_time, "%I:%M %p").strftime("%I:%M %p")
        else:
            formatted_time = datetime.strptime(preferred_time, "%H:%M").strftime("%I:%M %p")
    except Exception as e:
        logging.error(f"Error formatting time: {e}")
        formatted_time = "07:30 AM"  # Default on error
    
    form_data = {
        "zipcode": user["zipcode"] or "",
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
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
        
        if user is None:
            return jsonify({'error': 'User not found'}), 404
            
        if not user['zipcode']:
            return jsonify({'error': 'No zipcode set'}), 400
        
        weather_data_f = get_weather(zipcode=user['zipcode'])
        if not weather_data_f:
            return jsonify({'error': 'Unable to fetch weather data'}), 500
            
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
            'icon_url': icon_url
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
            form_data = request.form.to_dict()
            logging.info(f"Received profile update data: {form_data}")
            
            phone = format_phone_number(form_data.get('phone', ''))
            preferred_time = form_data.get('preferred_time', '07:30 AM')
            formatted_time = datetime.strptime(preferred_time, "%I:%M %p").strftime("%H:%M")
            
            # Update user
            db = get_db()
            db.execute('''
                UPDATE users 
                SET zipcode = ?,
                    phone_number = ?,
                    preferred_time = ?
                WHERE id = ?
            ''', [
                form_data.get('zipcode'),
                phone,
                formatted_time,
                session['user_id']
            ])
            db.commit()
            
            # Update scheduler with new time
            try:
                dt = datetime.strptime(preferred_time, "%I:%M %p")
                hour, minute = dt.hour, dt.minute
                
                global scheduler
                if scheduler is None or not scheduler.running:
                    scheduler = init_scheduler()
                else:
                    job = scheduler.add_job(
                        func=send_daily_weather_update,
                        trigger='cron',
                        hour=hour,
                        minute=minute,
                        id='daily_weather_job',
                        replace_existing=True
                    )
                    logging.info(f"[SCHEDULER] Job rescheduled for {hour:02d}:{minute:02d}")
                
            except Exception as e:
                logging.error(f"[SCHEDULER] Error updating schedule: {e}")
            
            return jsonify({'message': 'Profile updated successfully'})
            
        except Exception as e:
            logging.error(f"Profile update error: {e}")
            return jsonify({'error': str(e)}), 500

    # Handle GET request
    try:
        user = get_db().execute('SELECT * FROM users WHERE id = ?', 
                              [session['user_id']]).fetchone()
        user_dict = dict(user)
        
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
        }
        return render_template('profile.html', form_data=form_data)
    except Exception as e:
        logging.error(f"Error loading profile: {e}")
        return redirect(url_for('login'))

@app.route('/next-run')
def next_run():
    """Check when the next message will be sent."""
    try:
        jobs = scheduler.get_jobs()
        weather_job = next((job for job in jobs if job.id == 'daily_weather_job'), None)
        
        if weather_job:
            next_run = weather_job.next_run_time
            current_time = datetime.now(pytz.timezone('America/Chicago'))
            
            return jsonify({
                'status': 'scheduled',
                'current_time': str(current_time),
                'next_run': str(next_run),
                'scheduler_running': scheduler.running
            })
        else:
            return jsonify({
                'status': 'no_job',
                'scheduler_running': scheduler.running if scheduler else False
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    logging.info(f"[SMS] Starting send process for {to_number}")
    logging.info(f"[SMS] Message: {message_body}")
    
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
    
    if not all([account_sid, auth_token, twilio_number]):
        missing = []
        if not account_sid: missing.append("TWILIO_ACCOUNT_SID")
        if not auth_token: missing.append("TWILIO_AUTH_TOKEN")
        if not twilio_number: missing.append("TWILIO_PHONE_NUMBER")
        logging.error(f"[SMS] Missing credentials: {', '.join(missing)}")
        return False
    
    try:
        formatted_number = format_phone_number(to_number)
        logging.info(f"[SMS] Formatted number: {formatted_number}")
        
        client = Client(account_sid, auth_token)
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

def send_daily_weather_update(user_id=None):
    current_time = datetime.now(pytz.timezone('America/Chicago'))
    logging.info(f"[SCHEDULER] Weather update triggered at {current_time}")
    
    if hasattr(send_daily_weather_update, 'is_running') and send_daily_weather_update.is_running:
        logging.info("[SCHEDULER] Already processing, skipping this run")
        return
        
    send_daily_weather_update.is_running = True
    
    try:
        with app.app_context():
            db = get_db()
            users = []
            
            try:
                if user_id is not None:
                    user = db.execute('SELECT * FROM users WHERE id = ?', [user_id]).fetchone()
                    if user:
                        users = [user]
                    logging.info(f"[SCHEDULER] Processing single user: {user_id}")
                else:
                    users = db.execute('SELECT * FROM users').fetchall()
                    logging.info(f"[SCHEDULER] Processing all users: {len(users) if users else 0}")
            except Exception as e:
                logging.error(f"[SCHEDULER] Database error: {str(e)}")
                return
            
            if not users:
                logging.info("[SCHEDULER] No users found in database")
                return
                
            for user in users:
                try:
                    if user is None:
                        continue
                    user_dict = dict(user)
                    logging.info(f"[SCHEDULER] Processing user: {user_dict['id']}")
                    
                    weather_data = get_weather(zipcode=user_dict['zipcode'])
                    message = generate_weather_message(user_dict, weather_data)
                    
                    logging.info(f"[SCHEDULER] Sending message to {user_dict['phone_number']}")
                    result = send_text_message(user_dict['phone_number'], message)
                    logging.info(f"[SCHEDULER] Message sent result: {result}")
                    
                except Exception as e:
                    logging.error(f"[SCHEDULER] Error processing user: {str(e)}")
                    continue
    except Exception as e:
        logging.error(f"[SCHEDULER] Critical error: {str(e)}")
        logging.exception("[SCHEDULER] Full stack trace:")
    finally:
        send_daily_weather_update.is_running = False

def get_user_preferred_time():
    with app.app_context():
        try:
            db = get_db()
            user = db.execute('SELECT preferred_time FROM users LIMIT 1').fetchone()
            if user and user['preferred_time']:
                time_str = user['preferred_time']
                # Convert string to hour/minute
                if ":" in time_str:
                    if "AM" in time_str.upper() or "PM" in time_str.upper():
                        dt = datetime.strptime(time_str, "%I:%M %p")
                    else:
                        dt = datetime.strptime(time_str, "%H:%M")
                    return dt.hour, dt.minute
            return 7, 30  # Default to 7:30 AM
        except Exception as e:
            logging.error(f"Error getting preferred time: {e}")
            return 7, 30

# Global scheduler instance
scheduler = None

def init_scheduler():
    global scheduler
    
    jobstores = {
        'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
    }
    
    if scheduler is not None:
        scheduler.shutdown()
    
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        timezone=pytz.timezone('America/Chicago'),
        daemon=True
    )
    
    scheduler.start()
    logging.info("[SCHEDULER] Scheduler initialized")

    # Schedule job immediately after initialization
    try:
        with app.app_context():
            db = get_db()
            user = db.execute('SELECT preferred_time FROM users LIMIT 1').fetchone()
            if user and user['preferred_time']:
                time_str = user['preferred_time']
                if ":" in time_str:
                    hour, minute = map(int, time_str.split(":"))
                    job = scheduler.add_job(
                        func=send_daily_weather_update,
                        trigger='cron',
                        hour=hour,
                        minute=minute,
                        id='daily_weather_job',
                        replace_existing=True
                    )
                    logging.info(f"[SCHEDULER] Job scheduled for {hour:02d}:{minute:02d}")
                    logging.info(f"[SCHEDULER] Next run at: {job.next_run_time}")
    except Exception as e:
        logging.error(f"[SCHEDULER] Error scheduling job: {e}")

    return scheduler

@app.route('/scheduler-debug')
def scheduler_debug():
    """Debug endpoint to check scheduler status."""
    try:
        jobs = scheduler.get_jobs()
        now = datetime.now(pytz.timezone('America/Chicago'))
        return jsonify({
            'scheduler_running': scheduler.running,
            'current_time': str(now),
            'jobs': [{
                'id': job.id,
                'next_run_time': str(job.next_run_time),
                'trigger': str(job.trigger),
                'pending': job.pending
            } for job in jobs],
            'timezone': str(scheduler.timezone)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test-message-now')
def test_message_now():
    """Test endpoint to send message immediately."""
    try:
        with app.app_context():
            db = get_db()
            users = db.execute('SELECT * FROM users').fetchall()
            
            if not users:
                return jsonify({"status": "error", "message": "No users found"})
            
            results = []
            for user in users:
                try:
                    user_dict = dict(user)
                    logging.info(f"[TEST] Sending message to user: {user_dict['phone_number']}")
                    
                    weather_data = get_weather(zipcode=user_dict['zipcode'])
                    message = generate_weather_message(user_dict, weather_data)
                    
                    result = send_text_message(user_dict['phone_number'], message)
                    results.append({
                        "phone": user_dict['phone_number'],
                        "success": result,
                        "message": message
                    })
                    
                except Exception as e:
                    logging.error(f"[TEST] Error processing user: {str(e)}")
                    results.append({
                        "phone": user_dict.get('phone_number', 'unknown'),
                        "success": False,
                        "error": str(e)
                    })
            
            return jsonify({
                "status": "complete",
                "results": results
            })
            
    except Exception as e:
        logging.error(f"[TEST] Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/scheduler-status')
def scheduler_status():
    """Check scheduler status."""
    try:
        jobs = scheduler.get_jobs()
        return jsonify({
            'scheduler_running': scheduler.running,
            'current_time': str(datetime.now(pytz.timezone('America/Chicago'))),
            'jobs': [{
                'id': job.id,
                'next_run_time': str(job.next_run_time),
                'trigger': str(job.trigger)
            } for job in jobs],
            'timezone': str(scheduler.timezone)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

@app.route('/schedule-user-jobs')
def schedule_user_jobs():
    """Schedule individual jobs for each user."""
    try:
        with app.app_context():
            db = get_db()
            users = db.execute('SELECT * FROM users').fetchall()
            logging.info(f"[SCHEDULER] Found {len(users)} users to schedule")
            
            for user in users:
                try:
                    user_dict = dict(user)
                    logging.info(f"[SCHEDULER] Processing user: {user_dict}")
                    
                    preferred_time = user_dict['preferred_time']
                    if not preferred_time:
                        logging.error(f"[SCHEDULER] No preferred time for user {user_dict['id']}")
                        continue
                        
                    try:
                        if ":" in preferred_time:
                            hour, minute = map(int, preferred_time.split(":"))
                        else:
                            logging.error(f"[SCHEDULER] Invalid time format: {preferred_time}")
                            continue
                            
                        job = scheduler.add_job(
                            func=send_daily_weather_update,
                            trigger='cron',
                            hour=hour,
                            minute=minute,
                            args=[user_dict['id']],
                            id=f'weather_update_{user_dict["id"]}',
                            replace_existing=True
                        )
                        logging.info(f"[SCHEDULER] Job scheduled for user {user_dict['id']} at {hour}:{minute}")
                        logging.info(f"[SCHEDULER] Next run: {job.next_run_time}")
                    except ValueError as e:
                        logging.error(f"[SCHEDULER] Time parsing error for user {user_dict['id']}: {e}")
                except Exception as e:
                    logging.error(f"[SCHEDULER] Error processing user {user_dict['id']}: {e}")
                    logging.exception("[SCHEDULER] Full exception details:")
            
            # Log all scheduled jobs
            logging.info("[SCHEDULER] Current jobs:")
            scheduler.print_jobs()
            
            return jsonify({"status": "success", "users_processed": len(users)})
    except Exception as e:
        logging.error(f"[SCHEDULER] Error in schedule_user_jobs: {e}")
        logging.exception("[SCHEDULER] Full exception details:")
        return jsonify({"error": str(e)}), 500

@app.route('/test-scheduler-now')
def test_scheduler_now():
    """Force the scheduler to run immediately."""
    try:
        logging.info("[TEST] Running scheduler test immediately")
        send_daily_weather_update()
        return jsonify({
            "status": "success",
            "message": "Scheduler test completed - check logs for details"
        })
    except Exception as e:
        logging.error(f"[TEST] Scheduler test failed: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    with app.app_context():
        if not os.path.exists(DATABASE):
            logging.info("[INIT] Creating new database")
            init_db()
        else:
            try:
                db = get_db()
                db.execute("SELECT 1 FROM users LIMIT 1")
                logging.info("[INIT] Database tables verified")
            except sqlite3.OperationalError:
                logging.info("[INIT] Tables missing, initializing database")
                init_db()

        scheduler = init_scheduler()
        
        # Schedule initial job
        try:
            hour, minute = get_user_preferred_time()
            job = scheduler.add_job(
                func=send_daily_weather_update,
                trigger='cron',
                hour=hour,
                minute=minute,
                id='daily_weather_job',
                replace_existing=True
            )
            logging.info(f"[SCHEDULER] Initial job scheduled for {hour:02d}:{minute:02d}")
        except Exception as e:
            logging.error(f"[SCHEDULER] Initial job scheduling failed: {e}")

    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))