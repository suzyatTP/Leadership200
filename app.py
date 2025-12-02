import os
import json
import re
from io import BytesIO
from flask import Flask, send_file, request, jsonify
import psycopg2
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors

# -------------------------------------------------------------------
# DATABASE SETUP
# -------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


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
        "gifts": [],
    }


def ensure_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leadership_state (
            id SERIAL PRIMARY KEY,
            state_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("SELECT id FROM leadership_state LIMIT 1;")
    if not cur.fetchone():
        cur.execute("INSERT INTO leadership_state (state_json) VALUES (%s);", (json.dumps(default_state()),))
    conn.commit()
    cur.close()
    conn.close()


app = Flask(__name__)
ensure_table()

# -------------------------------------------------------------------
# ROUTES
# -------------------------------------------------------------------

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
    return jsonify(row[0] if row else default_state())


@app.route("/api/state", methods=["POST"])
def save_state():
    state = request.get_json()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM leadership_state LIMIT 1;")
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE leadership_state SET state_json=%s, updated_at=NOW() WHERE id=%s;", (json.dumps(state), row[0]))
    else:
        cur.execute("INSERT INTO leadership_state (state_json) VALUES (%s);", (json.dumps(state),))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})

# -------------------------------------------------------------------
# PDF GENERATION
# -------------------------------------------------------------------

def _parse_number(v):
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^\d.]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return 0


def _gift_base(label):
    m = re.search(r"\$([\d,]+)", label or "")
    return int(m.group(1).replace(",", "")) if m else 0


def build_pdf(state):
    goal = _parse_number(state.get("goal"))
    title = state.get("title", "LEADERSHIP 200")
    rows = state.get("rows", [])
    gifts = state.get("gifts", [])
    total_received = sum(_parse_number(g.get("amount")) for g in gifts if g.get("amount"))

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)

    # COLORS
    BLUE = colors.HexColor("#0047b5")
    BLUE_DARK = colors.HexColor("#00308b")
    BLUE_MID = colors.HexColor("#4f7fd6")
    RED = colors.HexColor("#c6001a")
    RED_LIGHT = colors.HexColor("#ff7a7a")
    TRI1 = colors.HexColor("#d5ecff")
    TRI2 = colors.HexColor("#e3f3ff")
    TRI3 = colors.HexColor("#edf8ff")
    BEIGE = colors.HexColor("#f3ede5")

    center = width / 2
    margin_x = 50

    # HEADER
    y = height - 36
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 8)
    c.drawCentredString(center, y, "TURNING POINT WITH DR. DAVID JEREMIAH")
    y -= 20
    c.setFont("Times-Bold", 26)
    c.setFillColor(colors.HexColor("#9f1515"))
    c.drawCentredString(center, y, title.upper())
    y -= 16
    c.setFont("Times-Roman", 9)
    c.setFillColor(colors.black)
    c.drawCentredString(center, y, "ACCELERATE YOUR VISION")
    y -= 14
    c.setFont("Times-Italic", 7.5)
    c.drawCentredString(center, y, "Delivering the Unchanging Word of God to an Ever-Changing World")

    # TOP STRIP
    strip_y = y - 8
    c.setFillColor(BEIGE)
    c.rect(0, strip_y - 24, width, 24, stroke=0, fill=1)
    c.setFont("Times-Roman", 7)
    c.setFillColor(colors.black)
    c.drawRightString(width - 230, strip_y - 4, "TOTAL RECEIVED TO DATE")
    c.setFillColor(BLUE_DARK)
    c.roundRect(width - 210, strip_y - 21, 170, 18, 3, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 10)
    c.drawCentredString(width - 125, strip_y - 17, f"${total_received:,.0f}")

    # TRIANGLE (narrower width)
    tri_top = strip_y - 45
    tri_height = 300
    tri_half_width = width * 0.35
    tri_base_y = tri_top - tri_height

    c.saveState()
    path = c.beginPath()
    path.moveTo(center, tri_top)
    path.lineTo(center - tri_half_width, tri_base_y)
    path.lineTo(center + tri_half_width, tri_base_y)
    path.close()
    c.clipPath(path, stroke=0, fill=0)

    for i in range(180):
        t = i / 179.0
        if t < 0.55:
            col = TRI1
        elif t < 0.82:
            col = TRI1.blend(TRI2, (t - 0.55) / 0.27)
        else:
            col = TRI2.blend(TRI3, (t - 0.82) / 0.18)
        yb = tri_base_y + t * tri_height
        c.setFillColor(col)
        c.rect(center - tri_half_width, yb, tri_half_width * 2, tri_height / 180, stroke=0, fill=1)
    c.restoreState()

    # TOTAL in triangle
    c.setFont("Times-Roman", 8)
    c.setFillColor(colors.black)
    c.drawCentredString(center, tri_top - 28, "TOTAL")
    c.setFont("Times-Bold", 18)
    c.drawCentredString(center, tri_top - 48, f"${goal:,.0f}")

    # BARS
    row_h = 17
    row_gap = 5
    n_rows = len(rows)
    step = row_h + row_gap
    bars_top = tri_base_y + tri_height - 55
    bar_left = margin_x
    bar_right = width - margin_x
    bar_width = bar_right - bar_left

    # TOP LABELS (improved readability)
    c.setFont("Times-Bold", 8.5)
    c.setFillColor(BLUE_DARK)
    c.drawString(bar_left, bars_top + 30, "Gifts Received / Needed")
    c.drawRightString(bar_right, bars_top + 30, "Total Gift / Pledge Dollars Committed")

    c.setFont("Times-Roman", 7.4)
    for i, r in enumerate(rows):
        yb = bars_top - i * step
        is_blue = i % 2 == 0
        steps = 140
        step_w = bar_width / steps
        for j in range(steps):
            t = j / (steps - 1)
            if is_blue:
                if t <= 0.18:
                    col = BLUE.blend(BLUE_MID, t / 0.18)
                elif t <= 0.5:
                    col = BLUE_MID.blend(colors.white, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = colors.white.blend(BLUE_MID, (t - 0.5) / 0.32)
                else:
                    col = BLUE_MID.blend(BLUE, (t - 0.82) / 0.18)
            else:
                if t <= 0.18:
                    col = RED.blend(RED_LIGHT, t / 0.18)
                elif t <= 0.5:
                    col = RED_LIGHT.blend(colors.white, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = colors.white.blend(RED_LIGHT, (t - 0.5) / 0.32)
                else:
                    col = RED_LIGHT.blend(RED, (t - 0.82) / 0.18)
            c.setFillColor(col)
            c.rect(bar_left + j * step_w, yb, step_w + 0.5, row_h, stroke=0, fill=1)

        # Left fraction
        c.setFillColor(colors.white)
        c.drawString(bar_left + 5, yb + 5.5, f"{r['received']}/{r['needed']}")
        # Center label
        c.setFillColor(BLUE_DARK)
        c.drawCentredString(bar_left + bar_width / 2, yb + 5.5, r["label"])
        # Right amount
        c.setFillColor(colors.white)
        c.drawRightString(bar_right - 5, yb + 5.5, "$0")

    # BOTTOM BANNER
    banner_w = width * 0.55
    banner_h = 22
    banner_x = (width - banner_w) / 2
    banner_y = bars_top - n_rows * step - 35
    c.setFillColor(BLUE_DARK)
    c.rect(banner_x, banner_y, banner_w, banner_h, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 11)
    total_gifts = sum(r["needed"] for r in rows)
    c.drawCentredString(width / 2, banner_y + 6, f"{total_gifts:,} LEADERSHIP GIFTS/PLEDGES")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


@app.route("/generate-pdf", methods=["GET"])
def generate_pdf():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()
    state = row[0] if row else default_state()
    pdf_buf = build_pdf(state)
    return send_file(pdf_buf, mimetype="application/pdf", as_attachment=False, download_name="Leadership200.pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
