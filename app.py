from flask import Flask, request, render_template, redirect, url_for, session, g, jsonify
import os
import sqlite3
import requests
import logging
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from openai import OpenAI  # Updated import

# Load environment variables
load_dotenv()

# Constants and OpenAI client setup
DATABASE = 'jacket_app.db'
OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)  # Initialize client once

# Exception classes
class WeatherAPIException(Exception):
    pass

# Utility functions
def generate_jacket_recommendation(temperature_f, wind_speed, condition):
    # Round values before generating recommendation
    temperature_f = round(temperature_f)
    wind_speed = round(wind_speed)
    
    prompt = f"The temperature is {temperature_f}°F with {condition}. Wind speed is {wind_speed}mph. What type of jacket should I wear?"
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Using standard OpenAI model
            messages=[
                {"role": "system", "content": "You are a weather assistant providing concise jacket recommendations."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=30,  # Shorter responses
            temperature=0.5  # Balanced between creativity and consistency
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        # Fallback recommendation
        if temperature_f < 50:
            return "Heavy jacket needed."
        return "No jacket needed."

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

def create_user(username, password, phone, zipcode, latitude, longitude, preferred_time):
    if not username or not password:
        raise ValueError("Username and password are required")
    
    db = get_db()
    hashed_password = generate_password_hash(password)
    db.execute(
        'INSERT INTO users (username, password, phone_number, zipcode, latitude, longitude, preferred_time) VALUES (?, ?, ?, ?, ?, ?, ?)',
        [username, hashed_password, phone, zipcode, latitude, longitude, preferred_time]
    )
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
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', [username]).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        return "Invalid username or password"
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        phone = request.form['phone']
        zipcode = request.form['zipcode']
        preferred_time = request.form['preferred_time']
        
        if not username or not password:
            return "Username and password are required"
            
        try:
            create_user(username, password, phone, zipcode, None, None, preferred_time)
            return redirect(url_for('login'))
        except ValueError as e:
            return str(e)
            
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
    form_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": user["preferred_time"],
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
        
    user = get_db().execute('SELECT * FROM users WHERE id = ?', [session['user_id']]).fetchone()
    form_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": user["preferred_time"],
        "latitude": user["latitude"],
        "longitude": user["longitude"],
        "temperature_sensitivity": user["temperature_sensitivity"]
    }
    return render_template('profile.html', form_data=form_data)

def get_weather(zipcode=None, latitude=None, longitude=None, units='imperial'):
    try:
        if zipcode:
            url = f"http://api.openweathermap.org/data/2.5/weather?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units={units}"
        elif latitude and longitude:
            url = f"http://api.openweathermap.org/data/2.5/weather?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units={units}"
        else:
            raise ValueError("Either zipcode or latitude/longitude must be provided")

        response = requests.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.error(f"Weather API error: {str(e)}")
        raise WeatherAPIException("Unable to fetch weather data")

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True)