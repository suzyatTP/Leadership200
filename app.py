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
    """Initial state used if DB is empty."""
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
    """Create the table if needed and insert one default row."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership_state (
            id SERIAL PRIMARY KEY,
            state_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    )
    cur.execute("SELECT id FROM leadership_state LIMIT 1;")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO leadership_state (state_json) VALUES (%s);",
            (json.dumps(default_state()),),
        )
    conn.commit()
    cur.close()
    conn.close()


app = Flask(__name__)
ensure_table()

# -------------------------------------------------------------------
# BASIC UTILS
# -------------------------------------------------------------------

def _parse_number(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _gift_base(label: str) -> int:
    """Extract base dollar amount from a row label."""
    m = re.search(r"\$([\d,]+)", label or "")
    return int(m.group(1).replace(",", "")) if m else 0


def _blend(c1, c2, t):
    """Linearly blend two ReportLab Color objects."""
    t = max(0.0, min(1.0, float(t)))
    r = c1.red + (c2.red - c1.red) * t
    g = c1.green + (c2.green - c1.green) * t
    b = c1.blue + (c2.blue - c1.blue) * t
    return colors.Color(r, g, b)


def _load_state_from_row(row):
    """Robustly convert the DB value to a Python dict."""
    if not row or row[0] is None:
        return default_state()
    raw = row[0]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return default_state()
    # JSONB may already be a Python object
    return raw


# -------------------------------------------------------------------
# ROUTES – STATE
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
    state = _load_state_from_row(row)
    return jsonify(state)


@app.route("/api/state", methods=["POST"])
def save_state():
    state = request.get_json()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM leadership_state LIMIT 1;")
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE leadership_state "
            "SET state_json=%s, updated_at=NOW() WHERE id=%s;",
            (json.dumps(state), row[0]),
        )
    else:
        cur.execute(
            "INSERT INTO leadership_state (state_json) VALUES (%s);",
            (json.dumps(state),),
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})


# -------------------------------------------------------------------
# PDF GENERATION
# -------------------------------------------------------------------

def build_pdf(state):
    """
    Render the Leadership 200 / Accelerate Your Vision scale-of-gifts
    page as a PDF using the same data that drives the web app.
    """
    goal = _parse_number(state.get("goal"))
    title = state.get("title", "LEADERSHIP 200")
    rows_state = state.get("rows", []) or []
    gifts_state = state.get("gifts", []) or []

    # --- Normalize gifts -------------------------------------------------
    gifts = []
    for g in gifts_state:
        amt = _parse_number(g.get("amount"))
        if amt <= 0:
            continue
        gifts.append(
            {
                "amount": amt,
                "donorName": g.get("donorName", ""),
                "idNumber": g.get("idNumber", ""),
                "purpose": g.get("purpose", ""),
            }
        )

    gifts.sort(key=lambda g: g["amount"], reverse=True)
    total_received_amount = sum(g["amount"] for g in gifts)

    # --- Build row infos & assign gifts to the correct level ------------
    row_infos = []
    for r in rows_state:
        base = _gift_base(r.get("label", ""))
        row_infos.append(
            {
                "state": r,
                "base": base,
                "needed": int(_parse_number(r.get("needed"))),
                "manual_received": int(_parse_number(r.get("received"))),
                "gift_count": 0,
                "gift_amount_sum": 0.0,
            }
        )

    # Gifts go to the first row whose base is <= amount and whose
    # previous row's base is > amount (or infinity for the first row).
    for gift in gifts:
        amount = gift["amount"]
        for idx, info in enumerate(row_infos):
            base = info["base"] or 0
            upper = row_infos[idx - 1]["base"] if idx > 0 else float("inf")
            if amount >= base and amount < upper:
                info["gift_count"] += 1
                info["gift_amount_sum"] += amount
                break

    total_needed = sum(info["needed"] for info in row_infos)

    # --- PDF canvas ------------------------------------------------------
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)
    center = width / 2.0
    margin_x = 50

    # Brand colors from Accelerate Your Vision palette
    BLUE_DARK = colors.HexColor("#006da3")   # deep blue
    BLUE_MID = colors.HexColor("#0073a9")    # mid blue
    BLUE_MAIN = colors.HexColor("#007dba")   # bright brand blue
    TEAL = colors.HexColor("#00878c")        # teal accent
    GOLD = colors.HexColor("#bb723a")        # copper / accent (not used heavily yet)
    AQUA_LIGHT = colors.HexColor("#eafdff")  # very light aqua
    WHITE = colors.white

    # ---------------- HEADER ----------------
    y = height - 30
    c.setFillColor(BLUE_DARK)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(center, y, "Turning Point Ministries with Dr. David Jeremiah")

    y -= 26
    c.setFont("Times-Bold", 30)
    c.setFillColor(BLUE_MAIN)
    c.drawCentredString(center, y, title.upper())

    y -= 16
    c.setFont("Times-Italic", 9)
    c.setFillColor(TEAL)
    c.drawCentredString(
        center,
        y,
        "Delivering the Unchanging Word of God to an Ever-Changing World",
    )

    # ---------------- TOP STRIP ----------------
    strip_y = y - 10
    c.setFillColor(AQUA_LIGHT)
    c.rect(0, strip_y - 24, width, 24, stroke=0, fill=1)

    label_center_x = width - 135
    c.setFont("Times-Roman", 9)
    c.setFillColor(BLUE_DARK)
    c.drawCentredString(label_center_x, strip_y + 2, "TOTAL RECEIVED TO DATE")

    # Blue pill with amount
    pill_width = 180
    pill_height = 18
    c.setFillColor(BLUE_DARK)
    c.roundRect(
        label_center_x - pill_width / 2.0,
        strip_y - pill_height + 1,
        pill_width,
        pill_height,
        4,
        stroke=0,
        fill=1,
    )
    c.setFillColor(WHITE)
    c.setFont("Times-Bold", 11)
    c.drawCentredString(
        label_center_x,
        strip_y - pill_height / 2.0 + 1,
        f"${total_received_amount:,.0f}",
    )

    # ---------------- TRIANGLE ----------------
    tri_top = strip_y - 48
    tri_height = 280
    tri_half_width = width * 0.35
    tri_base_y = tri_top - tri_height

    # Clip to triangle path
    c.saveState()
    path = c.beginPath()
    path.moveTo(center, tri_top)
    path.lineTo(center - tri_half_width, tri_base_y)
    path.lineTo(center + tri_half_width, tri_base_y)
    path.close()
    c.clipPath(path, stroke=0, fill=0)

    steps_tri = 200
    for i in range(steps_tri):
        t = i / float(steps_tri - 1)
        # Light aqua at the top, deepening into blue
        if t < 0.55:
            col = _blend(AQUA_LIGHT, BLUE_MAIN, t / 0.55)
        else:
            col = _blend(BLUE_MAIN, BLUE_DARK, (t - 0.55) / 0.45)
        yb = tri_base_y + t * tri_height
        c.setFillColor(col)
        c.rect(
            center - tri_half_width,
            yb,
            tri_half_width * 2,
            tri_height / steps_tri + 1,
            stroke=0,
            fill=1,
        )

    c.restoreState()

    # TOTAL text inside triangle
    c.setFont("Times-Roman", 11)
    c.setFillColor(BLUE_DARK)
    c.drawCentredString(center, tri_top - 4, "TOTAL")
    c.setFont("Times-Bold", 24)
    c.drawCentredString(center, tri_top - 24, f"${goal:,.0f}")

    # ---------------- BARS ----------------
    row_h = 24
    row_gap = 7
    step = row_h + row_gap
    n_rows = len(row_infos)

    bars_top = tri_base_y + tri_height - 55  # aligns with HTML layout
    bar_left = margin_x
    bar_right = width - margin_x
    bar_width = bar_right - bar_left

    # Top labels
    c.setFont("Times-Italic", 11)
    c.setFillColor(BLUE_DARK)
    c.drawString(bar_left, bars_top + 32, "Gifts Received / Needed")
    c.drawRightString(bar_right, bars_top + 32, "Total Gift / Pledge Dollars Committed")

    steps_bar = 160
    c.setFont("Times-Roman", 10)

    for idx, info in enumerate(row_infos):
        row_state = info["state"]
        yb = bars_top - idx * step

        needed = info["needed"]
        manual = info["manual_received"]
        auto = info["gift_count"]
        total_received_count = int(manual + auto)
        level_amount_received = info["gift_amount_sum"]

        is_blue_family = (idx % 2 == 0)

        # Bar gradient
        step_w = bar_width / float(steps_bar)
        for j in range(steps_bar):
            t = j / float(steps_bar - 1)
            if is_blue_family:
                # Deep blue → main blue → aqua → main blue → deep blue
                if t <= 0.18:
                    col = _blend(BLUE_DARK, BLUE_MAIN, t / 0.18)
                elif t <= 0.5:
                    col = _blend(BLUE_MAIN, AQUA_LIGHT, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = _blend(AQUA_LIGHT, BLUE_MAIN, (t - 0.5) / 0.32)
                else:
                    col = _blend(BLUE_MAIN, BLUE_DARK, (t - 0.82) / 0.18)
            else:
                # Teal → mid blue → aqua → mid blue → teal
                if t <= 0.18:
                    col = _blend(TEAL, BLUE_MID, t / 0.18)
                elif t <= 0.5:
                    col = _blend(BLUE_MID, AQUA_LIGHT, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = _blend(AQUA_LIGHT, BLUE_MID, (t - 0.5) / 0.32)
                else:
                    col = _blend(BLUE_MID, TEAL, (t - 0.82) / 0.18)

            c.setFillColor(col)
            c.rect(
                bar_left + j * step_w,
                yb,
                step_w + 0.5,
                row_h,
                stroke=0,
                fill=1,
            )

        # Left fraction text
        c.setFillColor(WHITE)
        c.drawString(
            bar_left + 5,
            yb + 8,
            f"{total_received_count}/{needed}",
        )

        # Center label
        c.setFillColor(BLUE_DARK)
        c.drawCentredString(
            bar_left + bar_width / 2.0,
            yb + 8,
            row_state.get("label", ""),
        )

        # Right amount
        c.setFillColor(WHITE)
        c.drawRightString(
            bar_right - 6,
            yb + 8,
            f"${level_amount_received:,.0f}",
        )

    # ---------------- BOTTOM BANNER ----------------
    banner_w = width * 0.55
    banner_h = 24
    banner_x = (width - banner_w) / 2.0
    banner_y = bars_top - n_rows * step - 16

    c.setFillColor(BLUE_DARK)
    c.rect(banner_x, banner_y, banner_w, banner_h, stroke=0, fill=1)

    c.setFillColor(WHITE)
    c.setFont("Times-Bold", 13)
    c.drawCentredString(
        width / 2.0,
        banner_y + 7,
        f"{total_needed:,} LEADERSHIP GIFTS/PLEDGES",
    )

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


@app.route("/generate-pdf", methods=["GET"])
def generate_pdf():
    """Generate PDF using the state stored in the database."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    state = _load_state_from_row(row)
    pdf_buf = build_pdf(state)

    return send_file(
        pdf_buf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="Leadership200.pdf",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
