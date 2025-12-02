import os
import json
from flask import Flask, send_file, request, jsonify
import psycopg2

# Render gives you this via the Environment variable
DATABASE_URL = os.environ.get("DATABASE_URL")

def extract_amount(label):
    """Extracts dollar value from label string like '1 Gift of $25,000,000'"""
    if not label:
        return 0
    import re
    m = re.search(r"\$([\d,]+)", label)
    if not m:
        return 0
    return int(m.group(1).replace(",", ""))

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

# ====== PDF GENERATION ROUTE =======================================
from reportlab.lib.pagesizes import letter, landscape
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

@app.route("/generate-pdf")
def generate_pdf():
    # Fetch latest state from DB
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    state = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    # ---------- PAGE SETUP (LANDSCAPE) ----------
    pdf_path = "/tmp/leadership_report.pdf"
    page_size = landscape(letter)
    c = canvas.Canvas(pdf_path, pagesize=page_size)
    width, height = page_size

    left_margin = 0.75 * inch
    right_margin = 0.75 * inch
    top_margin = height - 0.9 * inch

    title = state.get("title", "LEADERSHIP 200")
    rows = state.get("rows", [])
    gifts = state.get("gifts", [])

    # ---------- TOTALS ----------
    # Gifts total (for dark-blue pill)
    gifts_total = sum(g["amount"] for g in gifts)

    # Planned total + direct mail (for big "TOTAL")
    direct_mail = 3_000_000
    planned_total = 0
    for r in rows:
        gift_value = extract_amount(r["label"])
        planned_total += gift_value * r["needed"]
    triangle_total = planned_total + direct_mail

    total_needed = sum(r["needed"] for r in rows)

    # ---------- HEADER (match web) ----------
    # Small line above title
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(
        width / 2,
        top_margin + 24,
        "TURNING POINT WITH DR. DAVID JEREMIAH",
    )

    # Main red title
    c.setFillColorRGB(0.62, 0.0, 0.0)
    c.setFont("Times-Bold", 30)
    c.drawCentredString(width / 2, top_margin, title)

    # Subtitle
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 10)
    c.drawCentredString(width / 2, top_margin - 18, "ACCELERATE YOUR VISION")

    # Tagline
    c.setFont("Times-Italic", 8.5)
    c.drawCentredString(
        width / 2,
        top_margin - 32,
        "Delivering the Unchanging Word of God to an Ever-Changing World",
    )

    # ---------- TOP STRIP: TOTAL RECEIVED TO DATE ----------
    pill_width = 160
    pill_height = 24
    pill_right = width - right_margin
    pill_left = pill_right - pill_width
    pill_bottom = top_margin - 60
    pill_top = pill_bottom + pill_height

    # Label above pill
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 8)
    c.drawRightString(
        pill_right,
        pill_top + 6,
        "TOTAL RECEIVED TO DATE",
    )

    # Dark blue pill
    c.setFillColorRGB(0.0, 0.28, 0.71)
    c.roundRect(pill_left, pill_bottom, pill_width, pill_height, 4, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Times-Bold", 12)
    c.drawCentredString(
        (pill_left + pill_right) / 2,
        pill_bottom + 7,
        "${:,}".format(int(gifts_total)),
    )

    # ---------- TRIANGLE BACKGROUND ----------
    tri_top_y = top_margin - 90
    tri_base_y = tri_top_y - 190

    path = c.beginPath()
    path.moveTo(width / 2, tri_top_y)            # top
    path.lineTo(width * 0.06, tri_base_y)        # bottom left
    path.lineTo(width * 0.94, tri_base_y)        # bottom right
    path.close()

    c.setFillColorRGB(0.84, 0.92, 1.0)  # very light blue
    c.drawPath(path, fill=1, stroke=0)

    # "TOTAL" text inside triangle
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(width / 2, tri_top_y - 32, "TOTAL")

    c.setFont("Times-Bold", 20)
    c.drawCentredString(
        width / 2,
        tri_top_y - 52,
        "${:,}".format(int(triangle_total)),
    )

    # ---------- ROW LABELS ----------
    bars_top_y = tri_base_y - 16
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Italic", 7)
    c.drawString(left_margin, bars_top_y + 20, "Gifts Received/Needed")
    c.drawRightString(
        width - right_margin,
        bars_top_y + 20,
        "Total Gift/Pledge Dollars Committed",
    )

    # ---------- LEVEL BARS ----------
    bar_left = left_margin
    bar_right = width - right_margin
    bar_height = 16
    bar_gap = 6

    c.setFont("Times-Roman", 7.5)
    y = bars_top_y

    # Helper to get upper bound for gift range
    def upper_bound_for_index(idx):
        if idx == 0:
            return float("inf")
        return extract_amount(rows[idx - 1]["label"])

    for idx, r in enumerate(rows):
        label = r["label"]
        needed = r["needed"]
        rec = r["received"]
        base_amt = extract_amount(label)
        upper_amt = upper_bound_for_index(idx)

        # Amount actually received in this bracket
        level_sum = sum(
            g["amount"]
            for g in gifts
            if base_amt <= g["amount"] < upper_amt
        )

        # Background bar (alternating blue / red like on web)
        if idx % 2 == 0:
            c.setFillColorRGB(0.0, 0.28, 0.71)  # blue
        else:
            c.setFillColorRGB(0.78, 0.04, 0.19)  # red
        c.rect(bar_left, y, bar_right - bar_left, bar_height, fill=1, stroke=0)

        # Text in white on top of bar
        c.setFillColorRGB(1, 1, 1)

        # left: "received/needed"
        c.drawString(bar_left + 4, y + 4, f"{rec}/{needed}")

        # center: label
        c.drawCentredString((bar_left + bar_right) / 2, y + 4, label)

        # right: amount
        c.drawRightString(
            bar_right - 4,
            y + 4,
            "${:,}".format(int(level_sum)),
        )

        y -= (bar_height + bar_gap)

    # ---------- BOTTOM BLUE BANNER ----------
    banner_height = 20
    banner_width = 300
    banner_y = y - 26
    banner_x = (width - banner_width) / 2

    c.setFillColorRGB(0.0, 0.28, 0.71)
    c.rect(banner_x, banner_y, banner_width, banner_height, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Times-Bold", 10)
    c.drawCentredString(
        width / 2,
        banner_y + 5,
        f"{total_needed:,} LEADERSHIP GIFTS/PLEDGES",
    )

    # ---------- FINISH ----------
    c.showPage()
    c.save()

    # Open in a new tab (not forced download)
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="Leadership200.pdf",
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
