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
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def ensure_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership_state (
            id SERIAL PRIMARY KEY,
            state_json JSONB NOT NULL
        );
        """
    )
    cur.execute("SELECT COUNT(*) FROM leadership_state;")
    if cur.fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO leadership_state (state_json) VALUES (%s);",
            (json.dumps(default_state()),),
        )
    conn.commit()
    cur.close()
    conn.close()


# -------------------------------------------------------------------
# DEFAULT STATE (match your HTML structure)
# -------------------------------------------------------------------

def default_state():
    return {
        "goal": 100_000_000,
        "title": "LEADERSHIP 200",
        "rows": [
            {"label": "1 Gift of $25,000,000", "needed": 1, "received": 0},
            {"label": "1 Gift/Pledge of $20,000,000", "needed": 1, "received": 0},
            {"label": "1 Gift/Pledge of $10,000,000", "needed": 1, "received": 0},
            {"label": "1 Gift/Pledge of $5,000,000", "needed": 1, "received": 0},
            {"label": "2 Gifts/Pledges of $2,500,000", "needed": 2, "received": 0},
            {"label": "10 Gifts/Pledges of $1,000,000", "needed": 10, "received": 0},
            {"label": "14 Gifts/Pledges of $500,000", "needed": 14, "received": 0},
            {"label": "20 Gifts/Pledges of $250,000", "needed": 20, "received": 0},
            {"label": "50 Gifts/Pledges of $100,000", "needed": 50, "received": 0},
            {"label": "100 Gifts/Pledges of $50,000", "needed": 100, "received": 0},
        ],
        "gifts": [],
        "direct_mail": 3_000_000,
    }


app = Flask(__name__)
ensure_table()

# -------------------------------------------------------------------
# BASIC UTILS
# -------------------------------------------------------------------

def _parse_number(v):
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _gift_base(label):
    """
    Extract the base dollar amount from a row label, e.g.
    '1 Gift/Pledge of $10,000,000' -> 10_000_000.
    """
    if not label:
        return 0
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", label)
    if not m:
        return 0
    return _parse_number(m.group(1))


def _blend(c1, c2, t):
    """Linearly blend two ReportLab Color objects."""
    t = max(0.0, min(1.0, float(t)))
    r = c1.red + (c2.red - c1.red) * t
    g = c1.green + (c2.green - c1.green) * t
    b = c1.blue + (c2.blue - c1.blue) * t
    return colors.Color(r, g, b)

def _format_currency(amount):
    """Format a number like the JS formatCurrency helper ($1,234,567)."""
    return "$%s" % (format(int(round(float(amount or 0))), ',d'))


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
        except json.JSONDecodeError:
            return default_state()
    # JSONB may already be a Python object
    return raw


# -------------------------------------------------------------------
# ROUTES TO GET / SAVE STATE
# -------------------------------------------------------------------

@app.route("/get-state", methods=["GET"])
def get_state():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    state = _load_state_from_row(row)
    return jsonify(state)


@app.route("/save-state", methods=["POST"])
def save_state():
    data = request.get_json(force=True) or {}
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE leadership_state SET state_json = %s WHERE id = 1;",
        (json.dumps(data),),
    )
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO leadership_state (id, state_json) VALUES (1, %s);",
            (json.dumps(data),),
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok"})


# -------------------------------------------------------------------
# PDF GENERATION
# -------------------------------------------------------------------

def build_pdf(state):
    """Render the Leadership 200 top section as a PDF.
    This mirrors the browser logic so the PDF numbers match the webpage.
    """
    goal = _parse_number(state.get("goal"))
    title = state.get("title", "LEADERSHIP 200")
    rows = state.get("rows", []) or []
    gifts = state.get("gifts", []) or []

    # Normalise gifts from state (amount as float) and compute the grand total.
    parsed_gifts = []
    for g in gifts:
        amt = _parse_number(g.get("amount"))
        if amt <= 0:
            continue
        parsed_gifts.append({
            "amount": amt,
            "donorName": g.get("donorName", ""),
            "idNumber": g.get("idNumber", ""),
            "purpose": g.get("purpose", ""),
        })

    total_received = sum(g["amount"] for g in parsed_gifts)

    # Build row info objects the same way recalcAll() does in the front-end.
    row_infos = []
    for r in rows:
        base = _gift_base(r.get("label", ""))
        row_infos.append({"row": r, "base": base, "gifts": []})

    # Assign each gift into the appropriate level based on its amount.
    for gift in parsed_gifts:
        amt = gift["amount"]
        for idx, info in enumerate(row_infos):
            base = float(info["base"] or 0)
            upper = float(row_infos[idx - 1]["base"] or 0) if idx > 0 else float("inf")
            if amt >= base and amt < upper:
                info["gifts"].append(gift)
                break

    # Pre-compute counts and totals per row for easy rendering.
    for info in row_infos:
        r = info["row"]
        needed = int(_parse_number(r.get("needed")))
        manual_received = int(_parse_number(r.get("received")))
        auto_received = len(info["gifts"])
        info["needed"] = needed
        info["total_received"] = manual_received + auto_received
        info["amount_received"] = sum(g["amount"] for g in info["gifts"])

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)

    # Colors (matched to HTML)
    BLUE = colors.HexColor("#0047b5")
    BLUE_DARK = colors.HexColor("#00308b")
    BLUE_MID = colors.HexColor("#4f7fd6")
    RED = colors.HexColor("#c6001a")
    RED_LIGHT = colors.HexColor("#ff7a7a")
    TRI1 = colors.HexColor("#d5ecff")
    TRI2 = colors.HexColor("#e3f3ff")
    TRI3 = colors.HexColor("#edf8ff")
    BEIGE = colors.HexColor("#f3ede5")
    WHITE = colors.white

    center = width / 2.0
    margin_x = 50

    # ---------------- HEADER ----------------
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
    c.drawCentredString(
        center,
        y,
        "Delivering the Unchanging Word of God to an Ever-Changing World",
    )

    # ---------------- TOP STRIP ----------------
    strip_y = y - 8
    c.setFillColor(BEIGE)
    c.rect(0, strip_y - 24, width, 24, stroke=0, fill=1)

    label_center_x = width - 125
    c.setFont("Times-Roman", 9)
    c.setFillColor(colors.black)
    c.drawCentredString(label_center_x, strip_y + 1, "TOTAL RECEIVED TO DATE")

    c.setFillColor(BLUE_DARK)
    c.roundRect(width - 210, strip_y - 21, 170, 18, 3, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 11)
    c.drawCentredString(
        label_center_x,
        strip_y - 17,
        _format_currency(total_received),
    )

    # ---------------- TRIANGLE ----------------
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

    steps_tri = 180
    for i in range(steps_tri):
        t = i / float(steps_tri - 1)
        if t < 0.55:
            col = TRI1
        elif t < 0.82:
            col = TRI2
        else:
            col = TRI3
        y_band_top = tri_top - i * (tri_height / steps_tri)
        c.setFillColor(col)
        c.rect(
            center - tri_half_width,
            y_band_top - (tri_height / steps_tri),
            tri_half_width * 2,
            tri_height / steps_tri + 0.5,
            stroke=0,
            fill=1,
        )

    c.restoreState()

    # Triangle center text
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(center, tri_top - 20, "TOTAL")

    c.setFont("Times-Bold", 18)
    c.drawCentredString(center, tri_top - 40, f"${goal:,.0f}")

    # ---------------- BARS ----------------
    bars_top = tri_top - 72
    n_rows = len(row_infos)
    row_h = 18
    row_spacing = 3
    step = row_h + row_spacing

    bar_left = margin_x
    bar_right = width - margin_x
    bar_width = bar_right - bar_left

    # Top labels
    c.setFont("Times-Bold", 8.5)
    c.setFillColor(BLUE_DARK)
    c.drawString(bar_left, bars_top + 30, "Gifts Received / Needed")
    c.drawRightString(bar_right, bars_top + 30, "Total Gift / Pledge Dollars Committed")

    # Draw each bar + text
    c.setFont("Times-Roman", 7.4)
    steps_bar = 140

    for i, info in enumerate(row_infos):
        r = info["row"]
        yb = bars_top - i * step
        is_blue = (i % 2 == 0)
        step_w = bar_width / float(steps_bar)

        for j in range(steps_bar):
            t = j / float(steps_bar - 1)

            if is_blue:
                # blue → blue-mid → white → blue-mid → blue
                if t <= 0.18:
                    col = _blend(BLUE, BLUE_MID, t / 0.18)
                elif t <= 0.5:
                    col = _blend(BLUE_MID, WHITE, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = _blend(WHITE, BLUE_MID, (t - 0.5) / 0.32)
                else:
                    col = _blend(BLUE_MID, BLUE, (t - 0.82) / 0.18)
            else:
                # red → red-light → white → red-light → red
                if t <= 0.18:
                    col = _blend(RED, RED_LIGHT, t / 0.18)
                elif t <= 0.5:
                    col = _blend(RED_LIGHT, WHITE, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = _blend(WHITE, RED_LIGHT, (t - 0.5) / 0.32)
                else:
                    col = _blend(RED_LIGHT, RED, (t - 0.82) / 0.18)

            x0 = bar_left + j * step_w
            c.setFillColor(col)
            c.rect(
                x0,
                yb,
                step_w + 0.5,
                row_h,
                stroke=0,
                fill=1,
            )

        # Left fraction text
        c.setFillColor(colors.white)
        c.drawString(
            bar_left + 5,
            yb + 5.5,
            f"{info['total_received']}/{info['needed']}",
        )

        # Center label (dark blue so it stands out)
        c.setFillColor(BLUE_DARK)
        c.drawCentredString(
            bar_left + bar_width / 2.0,
            yb + 5.5,
            r.get("label", ""),
        )

        # Right amount – committed dollars per row
        c.setFillColor(colors.white)
        c.drawRightString(
            bar_right - 5,
            yb + 5.5,
            _format_currency(info['amount_received']),
        )

    # ---------------- BOTTOM BANNER ----------------
    banner_w = width * 0.55
    banner_h = 22
    banner_x = (width - banner_w) / 2.0
    banner_y = bars_top - n_rows * step - 35

    c.setFillColor(BLUE_DARK)
    c.rect(banner_x, banner_y, banner_w, banner_h, stroke=0, fill=1)

    total_gifts = sum(r.get("needed", 0) for r in rows)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 11)
    c.drawCentredString(
        width / 2.0,
        banner_y + 6,
        f"{total_gifts:,} LEADERSHIP GIFTS/PLEDGES",
    )

    # Finalize
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
