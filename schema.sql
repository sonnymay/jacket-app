DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS user_preferences;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    phone_number TEXT,
    zipcode TEXT,
    latitude REAL,
    longitude REAL,
    preferred_time TEXT
);

CREATE TABLE user_preferences (
    user_id INTEGER PRIMARY KEY,
    temperature_unit TEXT DEFAULT 'F',
    temperature_sensitivity TEXT DEFAULT 'Normal',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);