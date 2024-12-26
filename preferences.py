from flask import Blueprint, jsonify, request, session, g
import sqlite3

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect('jacket_app.db')
        g.db.row_factory = sqlite3.Row
    return g.db

preferences_bp = Blueprint('preferences', __name__)

def init_user_preferences(user_id):
    """Initialize preferences for new users"""
    db = get_db()
    try:
        db.execute(
            """INSERT OR IGNORE INTO user_preferences 
               (user_id, temperature_unit, temperature_sensitivity)
               VALUES (?, 'F', 'Normal')""",
            [user_id]
        )
        db.commit()
    except sqlite3.Error as e:
        print(f"Error initializing user preferences: {e}")

@preferences_bp.route('/api/preferences', methods=['GET'])
def get_preferences():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    db = get_db()
    try:
        cur = db.execute(
            """SELECT temperature_unit, temperature_sensitivity 
               FROM user_preferences 
               WHERE user_id = ?""",
            [session['user_id']]
        )
        prefs = cur.fetchone()
        if prefs is None:
            init_user_preferences(session['user_id'])
            return jsonify({
                'temperature_unit': 'F',
                'temperature_sensitivity': 'Normal'
            })
        return jsonify(dict(prefs))
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return jsonify({'error': 'Database error'}), 500

@preferences_bp.route('/api/preferences', methods=['POST'])
def update_preferences():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.get_json()
    temp_unit = data.get('temperature_unit', 'F')
    temp_sensitivity = data.get('temperature_sensitivity', 'Normal')
    if temp_unit not in ['F', 'C']:
        return jsonify({'error': 'Invalid temperature unit'}), 400
    if temp_sensitivity not in ['Cold', 'Normal', 'Warm']:
        return jsonify({'error': 'Invalid temperature sensitivity'}), 400
    db = get_db()
    try:
        db.execute(
            """INSERT OR REPLACE INTO user_preferences 
               (user_id, temperature_unit, temperature_sensitivity, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            [session['user_id'], temp_unit, temp_sensitivity]
        )
        db.commit()
        return jsonify({'status': 'success'})
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return jsonify({'error': 'Database error'}), 500

def with_user_preferences(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' in session:
            prefs = get_user_preferences(session['user_id'])
            return f(prefs, *args, **kwargs)
        return f(None, *args, **kwargs)
    return decorated_function