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
from datetime import datetime
from geopy.geocoders import Nominatim
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from pytz import timezone
import re

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

# Utility functions
def generate_jacket_recommendation(temperature_f, wind_speed, condition):
    """Generate a simple jacket recommendation."""
    temperature_f = round(temperature_f)
    wind_speed = round(wind_speed)

    prompt = (
        f"The temperature is {temperature_f}°F with {condition}. "
        f"Wind speed is {wind_speed} mph. Suggest a simple, clear jacket recommendation."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a weather assistant providing short and clear jacket advice."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=40,
            temperature=0.2
        )
        recommendation = response.choices[0].message.content.strip()
        logging.debug(f"OpenAI recommendation: {recommendation}")
        return recommendation
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
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
    """Validate that the phone number is a 10-digit US number."""
    # Remove any non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    return len(digits) == 10

def format_phone_number(phone):
    """Format phone number to E.164 format (+1XXXXXXXXXX)."""
    # Remove any non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    if not validate_phone(digits):
        raise ValueError("Invalid phone number. Please enter a 10-digit US phone number.")
    return f'+1{digits}'

def create_user(phone, password, zipcode, preferred_time, temperature_sensitivity):
    if not phone or not password:
        raise ValueError("Phone number and password are required")
    
    try:
        formatted_phone = format_phone_number(phone)
    except ValueError as e:
        raise ValueError(str(e))
    
    db = get_db()
    existing_user = db.execute('SELECT * FROM users WHERE phone_number = ?', [formatted_phone]).fetchone()
    if existing_user:
        raise ValueError("Phone number is already registered")
    
    hashed_password = generate_password_hash(password)
    db.execute('''
        INSERT INTO users (phone_number, password, zipcode, preferred_time, temperature_sensitivity) 
        VALUES (?, ?, ?, ?, ?)
    ''', [formatted_phone, hashed_password, zipcode, preferred_time, temperature_sensitivity])
    db.commit()

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
            phone = format_phone_number(request.form['phone'])
        except ValueError:
            return "Invalid phone number format"
            
        password = request.form['password']
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE phone_number = ?', [phone]).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        return "Invalid phone number or password"
    
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
        weather_notification_temp = request.form.get('weather_notification_temp')
        weather_notification_condition = request.form.get('weather_notification_condition')
        zipcode = request.form.get('zipcode')
        try:
            phone = format_phone_number(request.form.get('phone'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        preferred_time = request.form.get('preferred_time')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')

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
        ''', [weather_notification_temp, weather_notification_condition, 
              zipcode, phone, preferred_time, latitude, longitude, 
              session['user_id']])
        db.commit()
        return jsonify({'message': 'Profile updated successfully'})

    user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
    # Convert sqlite3.Row to dictionary for safer access
    user_dict = dict(user)
    
    form_data = {
        "zipcode": user_dict["zipcode"],
        "phone": user_dict["phone_number"],
        "preferred_time": user_dict["preferred_time"],
        "latitude": user_dict["latitude"],
        "longitude": user_dict["longitude"],
        "weather_notification_temp": user_dict.get("weather_notification_temp", 30),
        "weather_notification_condition": user_dict.get("weather_notification_condition", "Snow"),
        "temperature_sensitivity": user_dict.get("temperature_sensitivity", "Normal")
    }
    return render_template('profile.html', form_data=form_data)

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
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")
    
    try:
        # Ensure the number is formatted correctly
        formatted_to_number = format_phone_number(to_number)
        logging.info(f"Sending SMS to: {formatted_to_number}")
        logging.info(f"Using Twilio number: {twilio_number}")
        
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=message_body,
            from_=twilio_number,
            to=formatted_to_number
        )
        logging.info(f"Message sent successfully! SID: {message.sid}")
        return True
    except ValueError as e:
        logging.error(f"Phone number formatting error: {e}")
        return False
    except Exception as e:
        logging.error(f"Twilio error: {e}")
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
    logging.info("Starting daily weather update job")
    with app.app_context():
        db = get_db()
        users = db.execute('SELECT * FROM users').fetchall()
        logging.info(f"Found {len(users)} users to process")
        
        for user in users:
            try:
                logging.info(f"Processing user {user['id']}")
                weather_data = get_weather(zipcode=user['zipcode'])
                temperature = round(weather_data['main']['temp'])
                condition = weather_data['weather'][0]['main']
                
                logging.info(f"Weather for user {user['id']}: {temperature}°F, {condition}")

                if (temperature < user['weather_notification_temp'] or 
                    condition == user['weather_notification_condition']):
                    message = generate_weather_message(user, weather_data)
                    logging.info(f"Sending weather update to user {user['id']}")
                    send_text_message(user['phone_number'], message)
            except Exception as e:
                logging.error(f"Error processing user {user['id']}: {str(e)}")

@app.route('/test-openai')
def test_openai():
    """Test the OpenAI integration."""
    try:
        temperature_f = 32
        wind_speed = 10
        condition = "Snow"
        
        recommendation = generate_jacket_recommendation(temperature_f, wind_speed, condition)
        return jsonify({
            "temperature_f": temperature_f,
            "wind_speed": wind_speed,
            "condition": condition,
            "recommendation": recommendation
        })
    except Exception as e:
        logging.error(f"Error testing OpenAI: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/logout')
def logout():
    """Handle user logout by clearing session data."""
    session.clear()
    return redirect(url_for('login'))

# Initialize scheduler
jobstores = {
    'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
}
scheduler = BackgroundScheduler(jobstores=jobstores, timezone='America/Chicago')
logging.basicConfig()
logging.getLogger('apscheduler').setLevel(logging.DEBUG)
scheduler.add_job(
    func=send_daily_weather_update,
    trigger='cron',
    hour=7,
    minute=30,
    timezone='America/Chicago'
)
scheduler.start()

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))