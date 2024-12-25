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
from apscheduler.jobstores.base import JobLookupError
from openai import OpenAI  # Add this new import

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
def create_user(username, password, phone_number, zipcode, latitude, longitude, preferred_time):
    db = get_db()
    hashed_password = generate_password_hash(password)
    db.execute("INSERT INTO users (username, password, phone_number, zipcode, latitude, longitude, preferred_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
               [username, hashed_password, phone_number, zipcode, latitude, longitude, preferred_time])
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

def should_wear_jacket(weather_data):
    temperature = weather_data["main"]["temp"]
    if temperature < 59:
        return True
    else:
        return False
    
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
def generate_message(username, weather_data_fahrenheit, weather_data_celsius):
    temperature_f = weather_data_fahrenheit['main']['temp']
    temperature_c = weather_data_celsius['main']['temp']
    condition = weather_data_fahrenheit['weather'][0]['main']

    prompt = (
        f"Create a unique, engaging good morning text message for {username}, under 20 words. "
        f"Today's weather is {condition} with a temperature of {temperature_f}°F ({temperature_c}°C). "
        "Offer a personalized greeting and a brief, creative take on the day's weather, "
        "and make a playful jacket recommendation if it's cold."
    )

    print(f"Prompt sent to OpenAI: {prompt}")

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Use the model you have access to
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50,
            temperature=0.7
        )
        message = response.choices[0].message.content.strip()
        print(f"Generated message: {message}")
        return message
    except Exception as e:
        print(f"Error generating message with OpenAI: {e}")
        return f"Good Morning {username}! It's {temperature_f}°F ({temperature_c}°C) and {condition} today. Have a nice day!"

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

        message = generate_message(user["username"], weather_data_fahrenheit, weather_data_celsius)
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
    print("Profile route accessed")
    if "user_id" not in session:
        print("User not logged in, redirecting to login")
        return redirect(url_for("login"))

    user = get_user_by_id(session["user_id"])
    if user is None:
        print("User not found, redirecting to login")
        return redirect(url_for("login"))

    message = ""
    form_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": user["preferred_time"],
        "latitude": user["latitude"],
        "longitude": user["longitude"]
    }
    if request.method == "POST":
        zipcode = request.form["zipcode"]
        latitude = request.form["latitude"]
        longitude = request.form["longitude"]
        preferred_time_str = request.form["preferred_time"]

        print("Form submitted:")
        print(f"  Zipcode: {zipcode}")
        print(f"  Latitude: {latitude}")
        print(f"  Longitude: {longitude}")
        print(f"  Preferred Time: {preferred_time_str}")

        try:
            preferred_time = datetime.strptime(preferred_time_str, "%H:%M").time()
        except ValueError:
            message = "Invalid time format. Please use HH:MM."
            preferred_time = None

        if preferred_time:
            db = get_db()
            db.execute(
                "UPDATE users SET zipcode = ?, latitude = ?, longitude = ?, preferred_time = ? WHERE id = ?",
                [zipcode, latitude, longitude, preferred_time.strftime("%H:%M"), user["id"]]
            )
            db.commit()
            print("User profile updated in database")

            job_id = f"send_message_{user['id']}"
            scheduler.add_job(
                send_daily_message,
                "interval",
                days=1,
                start_date=datetime.combine(datetime.today(), preferred_time),
                args=[user["id"]],
                id=job_id,
                replace_existing=True,
            )
            message = "Profile updated successfully!"
            print("Profile updated, preparing to redirect")
            form_data.update({
                "zipcode": zipcode,
                "latitude": latitude,
                "longitude": longitude,
                "preferred_time": preferred_time_str
            })
            return jsonify({'message': message, 'form_data': form_data})

    print(f"Rendering index page with message: '{message}'")
    return render_template("profile.html", message=message, form_data=form_data)

@app.route("/login", methods=["GET", "POST"])
def login():
    print("Login route accessed")
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        print(f"Login attempt: username={username}, password={password}")

        user = get_user(username)
        print(f"User from database: {user}")

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            print(f"User ID stored in session: {session['user_id']}")

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

            print("Redirecting to index after successful login")
            return redirect(url_for("dashboard"))
        else:
            print("Invalid username or password")
            return "Invalid username or password"

    print("Rendering login page")
    return render_template("login.html")

@app.route("/logout")
def logout():
    print("Logout route accessed")
    user_id = session.pop("user_id", None)
    if user_id:
        job_id = f"send_message_{user_id}"
        try:
            scheduler.remove_job(job_id)
            print(f"Job {job_id} removed successfully.")
        except JobLookupError:
            print(f"Job {job_id} not found.")
    print("Redirecting to login after logout")
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    print("Register route accessed")
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        phone_number = request.form["phone"]
        zipcode = request.form["zipcode"]
        latitude = request.form["latitude"]
        longitude = request.form["longitude"]
        preferred_time_str = request.form["preferred_time"]

        try:
            preferred_time = datetime.strptime(preferred_time_str, "%H:%M").time()
        except ValueError:
            print("Invalid time format during registration")
            return "Invalid time format. Please use HH:MM."
        
        existing_user = get_user(username)
        if existing_user:
            print("Username already exists")
            return "Username already exists. Please choose a different one."

        create_user(username, password, phone_number, zipcode, latitude, longitude, preferred_time.strftime("%H:%M"))
        print("User registered, redirecting to login")
        return redirect(url_for("login"))

    print("Rendering register page")
    return render_template("register.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    print("Dashboard route accessed")
    if "user_id" not in session:
        print("User not logged in, redirecting to login")
        return redirect(url_for("login"))

    user = get_user_by_id(session["user_id"])
    if user is None:
        print("User not found, redirecting to login")
        return redirect(url_for("login"))

    message = ""

    try:
        weather_data_fahrenheit = {}
        weather_data_celsius = {}
        if user["latitude"] and user["longitude"]:
            weather_data_fahrenheit = get_weather(latitude=user["latitude"], longitude=user["longitude"])
            weather_data_celsius = get_weather_in_celsius(latitude=user["latitude"], longitude=user["longitude"])
        else:
            weather_data_fahrenheit = get_weather(zipcode=user["zipcode"])
            weather_data_celsius = get_weather_in_celsius(zipcode=user["zipcode"])

        current_weather = f"It's {weather_data_fahrenheit['main']['temp']} degrees and {weather_data_fahrenheit['weather'][0]['main']}."
        jacket_recommendation = "Yes, you should wear a jacket!" if should_wear_jacket(weather_data_fahrenheit) else "No, you don't need a jacket."
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        current_weather = "Weather data not available."
        jacket_recommendation = "Recommendation not available."

    next_message = "Not scheduled."
    for job in scheduler.get_jobs():
        if job.id == f"send_message_{user['id']}":
            next_message = job.next_run_time.strftime("%Y-%m-%d %H:%M")
            break
    
    user_data = {
        "zipcode": user["zipcode"],
        "phone": user["phone_number"],
        "preferred_time": user["preferred_time"],
        "latitude": user["latitude"],
        "longitude": user["longitude"]
    }
    return render_template("dashboard.html", message=message, form_data=user_data, current_weather=current_weather, jacket_recommendation=jacket_recommendation, next_message=next_message)

@app.route('/weather')
def get_current_weather():
    print("Weather route accessed")
    if "user_id" not in session:
        print("User not logged in, redirecting to login")
        return jsonify({'error': 'Not logged in'}), 401
    
    user = get_user_by_id(session["user_id"])
    if user is None:
        print("User not found")
        return jsonify({'error': 'User not found'}), 404
        
    try:
        if user["latitude"] and user["longitude"]:
            weather_data = get_weather(latitude=user["latitude"], longitude=user["longitude"])
        else:
            weather_data = get_weather(zipcode=user["zipcode"])
            
        current_weather = {
            'temperature': weather_data['main']['temp'],
            'condition': weather_data['weather'][0]['main'],
            'jacket_recommendation': "Yes" if should_wear_jacket(weather_data) else "No"
        }
        print(f"Returning weather data: {current_weather}")
        return jsonify(current_weather)
    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        return jsonify({'error': 'Weather data not available'}), 500

@app.route("/test_message")
def test_message():
    print("Test message route accessed")
    if "user_id" not in session:
        print("User not logged in, redirecting to login")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    send_daily_message(user_id)  # Send the message immediately
    
    return "Test message sent!"

if __name__ == "__main__":
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True)
