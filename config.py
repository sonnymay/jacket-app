import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class BaseConfig:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    
    OPENWEATHERMAP_API_KEY = os.getenv('OPENWEATHERMAP_API_KEY')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    DATABASE_NAME = 'jacket_app.db'
    
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300
    CACHE_THRESHOLD = 1000
    
    RATELIMIT_STORAGE_URL = os.getenv('RATELIMIT_STORAGE_URL', 'memory://')
    RATELIMIT_DEFAULT = "200 per day"
    RATELIMIT_LOGIN = "5 per minute"
    RATELIMIT_REGISTER = "3 per hour"
    
    WEATHER_CACHE_TIMEOUT = 1800  # 30 minutes
    DEFAULT_TEMP_UNIT = 'F'
    
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    LOG_FILE = 'app.log'
    LOG_MAX_BYTES = 100000
    LOG_BACKUP_COUNT = 3

class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TESTING = False
    SESSION_COOKIE_SECURE = False
    DATABASE_URI = f'sqlite:///{BaseConfig.DATABASE_NAME}'
    CACHE_DEFAULT_TIMEOUT = 60  # 1 minute

class TestingConfig(BaseConfig):
    DEBUG = True
    TESTING = True
    DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False

class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    DATABASE_URI = os.getenv('DATABASE_URL', f'sqlite:///{BaseConfig.DATABASE_NAME}')
    CACHE_TYPE = 'redis'
    CACHE_REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    PREFERRED_URL_SCHEME = 'https'

def get_config():
    env = os.environ.get('FLASK_ENV', 'development')
    config_map = {
        'development': DevelopmentConfig,
        'testing': TestingConfig,
        'production': ProductionConfig
    }
    return config_map.get(env, DevelopmentConfig)