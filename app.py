import os
import json
from flask import Flask, send_from_directory, request, jsonify
import psycopg2

# Render gives you this via the Environment variable
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def default_state():
    """Initial template used if DB is empty."""
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
        "gifts": [],
    }


def ensure_table():
    """Create leadership_state table and make sure there is one row."""
    conn = get_conn()
    cur = conn.cursor()

    # 1) Make sure table exists
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership_state (
            id SERIAL PRIMARY KEY,
            state_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    # 2) Make sure we have at least one row
    cur.execute("SELECT id FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO leadership_state (state_json) VALUES (%s);",
            (json.dumps(default_state()),),
        )

    conn.commit()
    cur.close()
    conn.close()


# Serve index.html from the project root (where your index.html lives)
app = Flask(__name__, static_folder=".")

# Initialize DB
ensure_table()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/state", methods=["GET"])
def get_state():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and row[0]:
        # Could be JSON already or string
        if isinstance(row[0], (dict, list)):
            return jsonify(row[0])
        return jsonify(json.loads(row[0]))

    # If no row (super rare), seed default again
    state = default_state()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO leadership_state (state_json) VALUES (%s);",
        (json.dumps(state),),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(state)


@app.route("/api/state", methods=["POST"])
def save_state():
    data = request.get_json() or {}

    conn = get_conn()
    cur = conn.cursor()

    # Update the single row
    cur.execute(
        """
        UPDATE leadership_state
           SET state_json = %s,
               updated_at = NOW()
         WHERE id = (SELECT id FROM leadership_state ORDER BY id LIMIT 1);
        """,
        (json.dumps(data),),
    )

    # If no row existed, insert one
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO leadership_state (state_json) VALUES (%s);",
            (json.dumps(data),),
        )

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
