DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS user_preferences;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    zipcode TEXT,
    latitude REAL,
    longitude REAL,
    preferred_time TEXT,
    weather_notification_temp INTEGER DEFAULT 30,
    weather_notification_condition TEXT DEFAULT 'Snow',
    temperature_sensitivity TEXT DEFAULT 'Normal'
);

CREATE TABLE user_preferences (
    user_id INTEGER PRIMARY KEY,
    temperature_unit TEXT DEFAULT 'F',
    temperature_sensitivity TEXT DEFAULT 'Normal',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);