import os
import json
from flask import Flask, send_from_directory, request, jsonify
import psycopg2

# Use Render's DATABASE_URL
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def ensure_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership_state (
            id SERIAL PRIMARY KEY,
            state_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()


ensure_table()

app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def default_state():
    return {
        "goal": 100000000,
        "title": "LEADERSHIP 200",
        "rows": [
            {"received": 0, "needed": 1, "label": "1 Gift of $25,000,000"},
            {"received": 0, "needed": 1, "label": "1 Gift/Pledge of $20,000,000"},
            {"received": 1, "needed": 1, "label": "1 Gift/Pledge of $10,000,000"},
            {"received": 0, "needed": 1, "label": "1 Gift/Pledge of $5,000,000"},
            {"received": 0, "needed": 2, "label": "2 Gifts/Pledges of $2,500,000"},
            {"received": 0, "needed": 10, "label": "10 Gifts/Pledges of $1,000,000"},
            {"received": 0, "needed": 14, "label": "14 Gifts/Pledges of $500,000"},
            {"received": 0, "needed": 20, "label": "20 Gifts/Pledges of $250,000"},
            {"received": 0, "needed": 50, "label": "50 Gifts/Pledges of $100,000"},
            {"received": 0, "needed": 100, "label": "100 Gifts/Pledges of $50,000"},
        ],
        "gifts": []   # ⭐ ADD THIS LINE TO ENABLE INDIVIDUAL GIFTS STORAGE
    }


@app.route("/api/state", methods=["GET"])
def get_state():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and row[0] is not None:
        return jsonify(row[0])
    else:
        return jsonify(default_state())


@app.route("/api/state", methods=["POST"])
def save_state():
    data = request.get_json() or {}

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE leadership_state
           SET state_json = %s,
               updated_at = NOW()
         WHERE id = (SELECT id FROM leadership_state ORDER BY id LIMIT 1);
        """,
        (json.dumps(data),),
    )

    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO leadership_state (state_json)
            VALUES (%s);
            """,
            (json.dumps(data),),
        )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
