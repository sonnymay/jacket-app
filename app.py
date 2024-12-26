import os
import requests
from dotenv import load_dotenv
from twilio.rest import Client
from flask import Flask, request, render_template, redirect, url_for, session, g, jsonify
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from openai import OpenAI, RateLimitError  # Updated OpenAI import
import logging
from logging.handlers import RotatingFileHandler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from monitoring import setup_monitoring, track_api_request
from config import get_config  # newly added import
from security import SecurityManager  # newly added import
import traceback          # newly added
from werkzeug.exceptions import HTTPException  # newly added
from preferences import preferences_bp  # newly added

DATABASE = 'jacket_app.db'

class WeatherAPIException(Exception):
    pass

class APIError(Exception):
    def __init__(self, message, status_code=400, payload=None):
        super().__init__()
        self.message = message
        self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        rv['status'] = self.status_code
        return rv

def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(error):
        response = jsonify(error.to_dict())
        response.status_code = error.status_code
        return response

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('404.html'), 404

    @app.errorhandler(429)
    def ratelimit_handler(error):
        return render_template('429.html'), 429

    @app.errorhandler(500)
    def internal_error(error):
        logging.error(f"Internal Server Error: {error}")
        logging.error(traceback.format_exc())
        return render_template('500.html'), 500

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        logging.error(f"Unexpected Error: {error}")
        logging.error(traceback.format_exc())
        return render_template('500.html'), 500

load_dotenv()

OPENWEATHERMAP_API_KEY = os.environ.get("OPENWEATHERMAP_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Configure logging
logging.basicConfig(
    handlers=[RotatingFileHandler('app.log', maxBytes=100000, backupCount=3)],
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
)

def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())  # replaced manual config loading

    setup_monitoring(app)
    limiter = Limiter(app=app, key_func=get_remote_address)

    # Cache configuration
    cache_config = {
        'CACHE_TYPE': 'simple',
        'CACHE_DEFAULT_TIMEOUT': 300,
        'CACHE_THRESHOLD': 1000
    }
    cache = Cache(app, config=cache_config)

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
    def validate_password(password):
        """Validate password complexity."""
        if len(password) < 8:
            return False, "Password must be at least 8 characters long"
        if not any(c.isupper() for c in password):
            return False, "Password must contain at least one uppercase letter"
        if not any(c.islower() for c in password):
            return False, "Password must contain at least one lowercase letter"
        if not any(c.isdigit() for c in password):
            return False, "Password must contain at least one number"
        return True, "Password is valid"

    def init_user_preferences(user_id):
        db = get_db()
        db.execute(
            """INSERT OR IGNORE INTO user_preferences 
            (user_id, temperature_unit, temperature_sensitivity)
            VALUES (?, 'F', 'Normal')""",
            [user_id]
        )
        db.commit()

    def create_user(username, password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity="Normal"):
        is_valid, message = validate_password(password)
        if not is_valid:
            raise ValueError(message)
        
        db = get_db()
        hashed_password = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (username, password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [username, hashed_password, phone_number, zipcode, latitude, longitude, preferred_time, temperature_sensitivity]
        )
        user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        init_user_preferences(user_id)
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
    @track_api_request('weather_api')
    @cache.memoize(timeout=app.config['CACHE_DEFAULT_TIMEOUT'])
    def get_weather(zipcode=None, latitude=None, longitude=None):
        try:
            if zipcode:
                url = f"http://api.openweathermap.org/data/2.5/weather?zip={zipcode},us&appid={OPENWEATHERMAP_API_KEY}&units=imperial"
            elif latitude and longitude:
                url = f"http://api.openweathermap.org/data/2.5/weather?lat={latitude}&lon={longitude}&appid={OPENWEATHERMAP_API_KEY}&units=imperial"
            else:
                raise ValueError("Either zipcode or latitude/longitude must be provided.")

            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Weather API error: {str(e)}")
            raise WeatherAPIException("Unable to fetch weather data")

    @track_api_request('weather_api')
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

    @track_api_request('forecast_api')
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

    @track_api_request('forecast_api')
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
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            if temperature_f < 30:
                recommendation_prompt = (
                    f"Give me a fun, casual style recommendation for very cold weather (below 30°F), "
                    f"{condition}, and wind speed of {wind_speed} mph. Include jacket, gloves, hat, "
                    "and a humorous touch."
                )
            elif temperature_f < 50:
                recommendation_prompt = (
                    f"Suggest a playful outfit for cold weather (30-49°F), {condition}, "
                    f"{wind_speed} mph wind. Mention gloves/hat if needed, and be a bit witty."
                )
            elif temperature_f < 60:
                recommendation_prompt = (
                    f"Offer a friendly, slightly humorous recommendation for cool weather (50-59°F), "
                    f"{condition}, and {wind_speed} mph wind. Jacket or no?"
                )
            else:
                recommendation_prompt = (
                    f"Give a lighthearted clothing recommendation for mild weather (above 60°F), "
                    f"{condition}, {wind_speed} mph wind. Should I pack a jacket?"
                )

            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Using GPT-4o-mini
                messages=[
                    {
                        "role": "system",
                        "content": "You are a weather assistant. Respond in 10 words or less."
                    },
                    {"role": "user", "content": recommendation_prompt}
                ],
                max_tokens=20,  # Reduced token limit
                temperature=0.7  # Slightly reduced temperature
            )

            recommendation = response.choices[0].message.content.strip()
            print(f"Generated recommendation: {recommendation}")
            return recommendation
        except RateLimitError as e:
            print(f"OpenAI API rate limit reached: {e}")
            logging.error(f"OpenAI API rate limit error: {e}")
            # Fallback to basic recommendations
            if temperature_f < 50:
                return "Heavy jacket, gloves, hat."
            elif temperature_f < 60:
                return "Light jacket advised."
            else:
                return "No jacket needed, maybe keep one handy."
        except Exception as e:
            print(f"Error generating jacket recommendation with OpenAI: {e}")
            logging.error(f"OpenAI API error: {e}")
            # Same fallback logic
            return "Dress for the weather."

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
    @track_api_request('openai_api')
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

        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Using GPT-4o-mini
                messages=[
                    {"role": "system", "content": "You are a friendly weather assistant."},
                    {"role": "user", "content": prompt}
                ]
            )
            message = response.choices[0].message.content.strip()
            return message
        except RateLimitError as e:
            print(f"OpenAI API rate limit reached: {e}")
            logging.error(f"OpenAI API rate limit error: {e}")
            return f"Good Morning {username}! Current weather: {condition}, {temperature_f}°F"
        except Exception as e:
            print(f"Error generating message with OpenAI: {e}")
            logging.error(f"OpenAI API error: {e}")
            return f"Good Morning {username}! Current weather: {condition}, {temperature_f}°F"

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

    security_manager = SecurityManager(app)  # initialize security

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
    @limiter.limit("5 per minute")
    def login():
        if request.method == "POST":
            try:
                username = request.form["username"]
                password = request.form["password"]
                logging.info(f"Login attempt for user: {username}")

                user = get_user(username)
                if user and check_password_hash(user["password"], password):
                    session["user_id"] = user["id"]
                    logging.info(f"Successful login for user: {username}")

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
                    logging.warning(f"Failed login attempt for user: {username}")
                    return "Invalid username or password", 401
            except Exception as e:
                logging.error(f"Login error: {str(e)}")
                return "An error occurred during login", 500

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

    register_error_handlers(app)
    app.register_blueprint(preferences_bp)

    return app

if __name__ == '__main__':
    app = create_app()
    if not os.path.exists(DATABASE):
        with app.app_context():
            init_db()
    app.run(debug=True)