import os
import json
from flask import Flask, send_file, request, jsonify
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
    """Create leadership_state table and make sure there is one row with default_state()."""
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

    # 2) Make sure there is at least one row
    cur.execute("SELECT id FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()

    if row is None:
        state = default_state()
        cur.execute(
            "INSERT INTO leadership_state (state_json) VALUES (%s);",
            (json.dumps(state),),
        )

    conn.commit()
    cur.close()
    conn.close()


# Serve index.html from the project root (NO static folder needed)
app = Flask(__name__)

# Initialize DB
ensure_table()


from flask import send_from_directory

@app.route("/")
def index():
    return send_file("index.html")


@app.route("/api/state", methods=["GET"])
def get_state():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and row[0]:
        if isinstance(row[0], (dict, list)):
            return jsonify(row[0])
        return jsonify(json.loads(row[0]))

    # If no row existed, reinsert default
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

def merge_state_with_template(incoming):
    """
    Keep the template rows (labels + needed) constant,
    only allow 'received' + gifts (and optional goal/title) to change.
    """
    base = default_state()

    if not incoming:
        return base

    # If you want to allow these to change, keep this:
    base["goal"] = incoming.get("goal", base["goal"])
    base["title"] = incoming.get("title", base["title"])

    incoming_rows = incoming.get("rows") or []

    for i, row in enumerate(base["rows"]):
        if i < len(incoming_rows):
            inc = incoming_rows[i] or {}
            # ONLY copy the received count from the incoming state
            if "received" in inc:
                row["received"] = inc["received"]
            # We intentionally ignore incoming 'label' and 'needed'
            # so those stay locked to the template.

    # Gifts list can be fully editable
    base["gifts"] = incoming.get("gifts", base["gifts"])

    return base

@app.route("/api/state", methods=["POST"])
def save_state():
    try:
        incoming = request.get_json()
        merged_state = merge_state_with_template(incoming)

        conn = get_conn()
        cur = conn.cursor()

        # Find existing row
        cur.execute("SELECT id FROM leadership_state ORDER BY id LIMIT 1;")
        row = cur.fetchone()

        if row is None:
            # No row yet → insert
            cur.execute(
                "INSERT INTO leadership_state (state_json) VALUES (%s);",
                (json.dumps(merged_state),),
            )
        else:
            # Update the existing row
            cur.execute(
                "UPDATE leadership_state SET state_json = %s, updated_at = NOW() WHERE id = %s",
                (json.dumps(merged_state), row[0]),
            )

        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        print("ERROR saving state:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/debug-db")
def debug_db():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT NOW();")
        ts = cur.fetchone()[0]
        cur.close()
        conn.close()
        return f"DB OK: {ts}"
    except Exception as e:
        return f"DB ERROR: {e}", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
