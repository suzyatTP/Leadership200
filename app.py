from flask import Flask, request, jsonify
import psycopg2
import os
import json

app = Flask(__name__)

# Connect to Render PostgreSQL using environment variable DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")

def get_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# Ensure table exists
def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS state (
            id SERIAL PRIMARY KEY,
            data JSONB NOT NULL
        );
    """)
    # Ensure exactly one row exists
    cur.execute("SELECT COUNT(*) FROM state;")
    count = cur.fetchone()[0]
    if count == 0:
        cur.execute("INSERT INTO state (data) VALUES ('{}');")
    conn.commit()
    cur.close()
    conn.close()

init_db()


@app.route("/api/state", methods=["GET"])
def load_state():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT data FROM state LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({})
    return jsonify(row[0])


@app.route("/api/state", methods=["POST"])
def save_state():
    data = request.json

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE state SET data = %s WHERE id = 1;", (json.dumps(data),))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "ok"})
    

@app.route("/")
def home():
    return app.send_static_file("index.html")
