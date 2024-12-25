import os
import requests
from dotenv import load_dotenv
from twilio.rest import Client
from flask import Flask, request, render_template, redirect, url_for, session, g, jsonify
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import openai
from openai import OpenAI

load_dotenv()

OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "a_default_secret_key")
DATABASE = 'jacket_app.db'

# Database helper functions
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

# User functions
def create_user(username, password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity="Normal"):
    db = get_db()
    hashed_password = generate_password_hash(password)
    db.execute(
        "INSERT INTO users (username, password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [username, hashed_password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity]
    )
    db.commit()

def get_user(username):
    db = get_db()
    cur = db.execute("SELECT * FROM users WHERE username = ?", [username])
    user = cur.fetchone()
    return user

def get_user_by_id(user_id):
    db = get_db()
    cur = db.execute("SELECT * FROM users WHERE id = ?", [user_id])
    user = cur.fetchone()
    return user

# Weather functions
def get_weather(zipcode=None, latitude=None, longitude=None):
    if zipcode:
        url = f"http://api.openweathermap.org/data/2.5/weather?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units=imperial"
    elif latitude and longitude:
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units=imperial"
    else:
        raise ValueError("Either zipcode or latitude/longitude must be provided.")

    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def get_weather_in_celsius(zipcode=None, latitude=None, longitude=None):
    if zipcode:
        url = f"http://api.openweathermap.org/data/2.5/weather?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units=metric"
    elif latitude and longitude:
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units=metric"
    else:
        raise ValueError("Either zipcode or latitude/longitude must be provided.")

    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def get_forecast(zipcode=None, latitude=None, longitude=None):
    if zipcode:
        url = f"http://api.openweathermap.org/data/2.5/forecast?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units=imperial"
    elif latitude and longitude:
        url = f"http://api.openweathermap.org/data/2.5/forecast?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units=imperial"
    else:
        raise ValueError("Either zipcode or latitude/longitude must be provided.")

    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def get_forecast_in_celsius(zipcode=None, latitude=None, longitude=None):
    if zipcode:
        url = f"http://api.openweathermap.org/data/2.5/forecast?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units=metric"
    elif latitude and longitude:
        url = f"http://api.openweathermap.org/data/2.5/forecast?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units=metric"
    else:
        raise ValueError("Either zipcode or latitude/longitude must be provided.")

    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def generate_jacket_recommendation(temperature_f, wind_speed, condition):
    try:
        openai.api_key = OPENAI_API_KEY
        if temperature_f < 30:
            recommendation_prompt = f"What type of jacket, gloves, and hat should someone wear in very cold weather (below 30°F) with {condition} and a wind speed of {wind_speed} mph?"
        elif temperature_f < 50:
            recommendation_prompt = f"What type of jacket should someone wear in cold weather (30-49°F) with {condition} and a wind speed of {wind_speed} mph? Should they wear gloves or a hat?"
        elif temperature_f < 60:
            recommendation_prompt = f"What type of jacket should someone wear in cool weather (50-59°F) with {condition} and a wind speed of {wind_speed} mph?"
        else:
            recommendation_prompt = f"What should someone wear in mild weather (above 60°F) with {condition} and a wind speed of {wind_speed} mph? Should they wear a jacket?"

        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful weather assistant providing specific clothing recommendations."},
                {"role": "user", "content": recommendation_prompt}
            ],
            max_tokens=100,
            temperature=0.7
        )
        recommendation = response.choices[0].message.content.strip()
        print(f"Generated recommendation: {recommendation}")
        return recommendation
    except Exception as e:
        print(f"Error generating jacket recommendation with OpenAI: {e}")
        if temperature_f < 50:
            return "You should wear a warm jacket today."
        elif temperature_f < 60:
            return "A light jacket would be appropriate."
        else:
            return "No jacket needed today."

def should_wear_jacket(weather_data):
    temperature = weather_data["main"]["temp"]
    wind_speed = weather_data["wind"]["speed"]
    condition = weather_data['weather'][0]['main']

    recommendation = generate_jacket_recommendation(temperature, wind_speed, condition)
    return recommendation

# SMS function
def send_sms(to_number, message):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    message = client.messages.create(
        to=to_number,
        from_=TWILIO_PHONE_NUMBER,
        body=message
    )
    print(f"SMS sent! SID: {message.sid}")

# Function to generate messages using OpenAI API
def generate_message(username, weather_data_fahrenheit, weather_data_celsius, user_preferences):
    temperature_f = weather_data_fahrenheit['main']['temp']
    temperature_c = weather_data_celsius['main']['temp']
    condition = weather_data_fahrenheit['weather'][0]['main']
    wind_speed = weather_data_fahrenheit['wind']['speed']
    humidity = weather_data_fahrenheit['main']['humidity']

    prompt = (
        f"Create a friendly good morning message for {username}. "
        f"Current weather: {condition}, {temperature_f}°F ({temperature_c}°C), "
        f"wind {wind_speed} mph, humidity {humidity}%. "
        "Include a brief weather description and clothing advice if needed. "
        "Keep it under 20 words and make it engaging."
    )

    # Get forecast data
    if user_preferences["latitude"] and user_preferences["longitude"]:
        forecast_data_fahrenheit = get_forecast(latitude=user_preferences["latitude"], longitude=user_preferences["longitude"])
        forecast_data_celsius = get_forecast_in_celsius(latitude=user_preferences["latitude"], longitude=user_preferences["longitude"])
    else:
        forecast_data_fahrenheit = get_forecast(zipcode=user_preferences["zipcode"])
        forecast_data_celsius = get_forecast_in_celsius(zipcode=user_preferences["zipcode"])

    # Extract morning and afternoon forecast
    morning_forecast = ""
    afternoon_forecast = ""
    for entry in forecast_data_fahrenheit['list']:
        dt_txt = entry['dt_txt']
        hour = datetime.strptime(dt_txt, '%Y-%m-%d %H:%M:%S').hour
        if 6 <= hour < 12:
            temp_f = entry['main']['temp']
            temp_c = (temp_f - 32) * 5 / 9
            morning_forecast = f"{entry['weather'][0]['main']} {temp_f:.0f}°F/{temp_c:.0f}°C"
        elif 12 <= hour < 18:
            temp_f = entry['main']['temp']
            temp_c = (temp_f - 32) * 5 / 9
            afternoon_forecast = f"{entry['weather'][0]['main']} {temp_f:.0f}°F/{temp_c:.0f}°C"
        if morning_forecast and afternoon_forecast:
            break

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a friendly weather assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        message = response.choices[0].message.content.strip()
        return message
    except Exception as e:
        print(f"Error generating message with OpenAI: {e}")
        return f"Good Morning {username}! Weather update: {current_weather}"

# Task to send daily messages
def send_daily_message(user_id):
    user = get_user_by_id(user_id)
    if not user:
        print(f"User not found: {user_id}")
        return

    try:
        if user["latitude"] and user["longitude"]:
            weather_data_fahrenheit = get_weather(latitude=user["latitude"], longitude=user["longitude"])
            weather_data_celsius = get_weather_in_celsius(latitude=user["latitude"], longitude=user["longitude"])
        else:
            weather_data_fahrenheit = get_weather(zipcode=user["zipcode"])
            weather_data_celsius = get_weather_in_celsius(zipcode=user["zipcode"])

        message = generate_message(user["username"], weather_data_fahrenheit, weather_data_celsius, user)
        send_sms(user["phone_number"], message)

    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

# Scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# Flask routes
@app.before_request
def require_login():
    allowed_routes = ['login', 'register', 'dashboard']
    if request.endpoint not in allowed_routes and 'user_id' not in session:
        return redirect(url_for('login'))

@app.route("/", methods=["GET", "POST"])
def index():
    return redirect(url_for("dashboard"))

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = get_user_by_id(session["user_id"])
    if user is None:
        return redirect(url_for("login"))

    message = ""
    form_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": user["preferred_time"],
        "latitude": user["latitude"],
        "longitude": user["longitude"],
        "temperature_sensitivity": user["temperature_sensitivity"]
    }

    if request.method == "POST":
        zipcode = request.form["zipcode"]
        latitude = request.form["latitude"]
        longitude = request.form["longitude"]
        preferred_time_str = request.form["preferred_time"]

        try:
            preferred_time = datetime.strptime(preferred_time_str, "%I:%M %p").strftime("%H:%M")
        except ValueError:
            message = "Invalid time format. Please use HH:MM AM/PM format."
            return jsonify({'error': message}), 400

        db = get_db()
        db.execute(
            "UPDATE users SET zipcode = ?, latitude = ?, longitude = ?, preferred_time = ? WHERE id = ?",
            [zipcode, latitude, longitude, preferred_time, user["id"]]
        )
        db.commit()

        job_id = f"send_message_{user['id']}"
        scheduler.add_job(
            send_daily_message,
            "interval",
            days=1,
            start_date=datetime.combine(datetime.today(), datetime.strptime(preferred_time, "%H:%M").time()),
            args=[user["id"]],
            id=job_id,
            replace_existing=True,
        )
        
        form_data.update({
            "zipcode": zipcode,
            "latitude": latitude,
            "longitude": longitude,
            "preferred_time": preferred_time_str
        })
        return jsonify({'message': 'Profile updated successfully', 'form_data': form_data})

    # Convert preferred_time to 12-hour format for display
    if form_data["preferred_time"]:
        form_data["preferred_time"] = datetime.strptime(form_data["preferred_time"], "%H:%M").strftime("%I:%M %p")
        
    return render_template("profile.html", message=message, form_data=form_data)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = get_user(username)
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]

            preferred_time = datetime.strptime(user["preferred_time"], "%H:%M").time()
            job_id = f"send_message_{user['id']}"
            scheduler.add_job(
                send_daily_message,
                'interval',
                days=1,
                start_date=datetime.combine(datetime.today(), preferred_time),
                args=[user["id"]],
                id=job_id,
                replace_existing=True
            )

            return redirect(url_for("dashboard"))
        else:
            return "Invalid username or password", 401

    return render_template("login.html")

@app.route("/logout")
def logout():
    user_id = session.pop("user_id", None)
    if user_id:
        job_id = f"send_message_{user_id}"
        try:
            scheduler.remove_job(job_id)
        except:
            pass
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        phone_number = request.form["phone"]
        zipcode = request.form["zipcode"]
        latitude = request.form["latitude"]
        longitude = request.form["longitude"]
        preferred_time_str = request.form["preferred_time"]
        temperature_sensitivity = request.form["temperature_sensitivity"]

        try:
            # Convert 12-hour format to 24-hour format for storage
            preferred_time = datetime.strptime(preferred_time_str, "%I:%M %p").strftime("%H:%M")
        except ValueError:
            return "Invalid time format. Please use HH:MM AM/PM format.", 400

        existing_user = get_user(username)
        if existing_user:
            return "Username already exists. Please choose a different one.", 400

        create_user(username, password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity)
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = get_user_by_id(session["user_id"])
    if user is None:
        return redirect(url_for("login"))

    try:
        if user["latitude"] and user["longitude"]:
            weather_data_fahrenheit = get_weather(latitude=user["latitude"], longitude=user["longitude"])
            weather_data_celsius = get_weather_in_celsius(latitude=user["latitude"], longitude=user["longitude"])
        else:
            weather_data_fahrenheit = get_weather(zipcode=user["zipcode"])
            weather_data_celsius = get_weather_in_celsius(zipcode=user["zipcode"])

        current_weather = (
            f"It's {weather_data_fahrenheit['main']['temp']:.1f}°F ({weather_data_celsius['main']['temp']:.1f}°C) and "
            f"{weather_data_fahrenheit['weather'][0]['main']}. Wind speed: {weather_data_fahrenheit['wind']['speed']} mph, "
            f"Humidity: {weather_data_fahrenheit['main']['humidity']}%"
        )
        
        jacket_recommendation = should_wear_jacket(weather_data_fahrenheit)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        current_weather = "Weather data not available."
        jacket_recommendation = "Recommendation not available."

    # Get next scheduled message time
    next_message = "Not scheduled."
    for job in scheduler.get_jobs():
        if job.id == f"send_message_{user['id']}":
            next_message = datetime.strftime(job.next_run_time, "%I:%M %p on %B %d, %Y")
            break

    # Format preferred time to 12-hour format with AM/PM
    preferred_time_12hr = datetime.strptime(user["preferred_time"], "%H:%M").strftime("%I:%M %p")

    # Prepare data for the template
    user_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": preferred_time_12hr,
        "latitude": user["latitude"],
        "longitude": user["longitude"]
    }

    return render_template(
        "dashboard.html",
        message="",
        form_data=user_data,
        current_weather=current_weather,
        jacket_recommendation=jacket_recommendation,
        next_message=next_message
    )

@app.route('/weather')
def get_current_weather():
    if "user_id" not in session:
        return jsonify({'error': 'Not logged in'}), 401

    user = get_user_by_id(session["user_id"])
    if user is None:
        return jsonify({'error': 'User not found'}), 404

    try:
        if user["latitude"] and user["longitude"]:
            weather_data_fahrenheit = get_weather(latitude=user["latitude"], longitude=user["longitude"])
            weather_data_celsius = get_weather_in_celsius(latitude=user["latitude"], longitude=user["longitude"])
        else:
            weather_data_fahrenheit = get_weather(zipcode=user["zipcode"])
            weather_data_celsius = get_weather_in_celsius(zipcode=user["zipcode"])

        feels_like_f = weather_data_fahrenheit['main']['feels_like']
        feels_like_c = weather_data_celsius['main']['feels_like']

        current_weather = {
            'temperature_f': round(weather_data_fahrenheit['main']['temp'], 1),
            'temperature_c': round(weather_data_celsius['main']['temp'], 1),
            'condition': weather_data_fahrenheit['weather'][0]['main'],
            'description': weather_data_fahrenheit['weather'][0]['description'],
            'wind_speed': weather_data_fahrenheit['wind']['speed'],
            'humidity': weather_data_fahrenheit['main']['humidity'],
            'feels_like_f': round(feels_like_f, 1),
            'feels_like_c': round(feels_like_c, 1),
            'jacket_recommendation': should_wear_jacket(weather_data_fahrenheit),
            'icon': weather_data_fahrenheit['weather'][0]['icon'],
            'preferred_time': datetime.strptime(user["preferred_time"], "%H:%M").strftime("%I:%M %p")
        }

        # Get next scheduled message time
        for job in scheduler.get_jobs():
            if job.id == f"send_message_{user['id']}":
                current_weather['next_scheduled_message'] = datetime.strftime(
                    job.next_run_time, 
                    "%I:%M %p on %B %d, %Y"
                )
                break

        return jsonify(current_weather)

    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        return jsonify({'error': 'Weather data not available'}), 500
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({'error': 'An unexpected error occurred'}), 500

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True)