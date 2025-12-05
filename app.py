import os
import json
import re
from io import BytesIO

from flask import Flask, send_file, request, jsonify
import psycopg2

# PDF libs
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors

# -------------------------------------------------------------------
# DATABASE / STATE
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
        return 0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _gift_base(label: str) -> float:
    """Extract base dollar amount from a row label (e.g. '$10,000,000' -> 10000000)."""
    if not label:
        return 0.0
    m = re.search(r"\$([\d,]+(\.\d+)?)", label)
    if not m:
        return 0.0
    return _parse_number(m.group(1))


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


def _format_currency(amount):
    """Match the JS formatCurrency: $1,234,567 with no decimals."""
    return "$" + format(int(round(_parse_number(amount))), ",d")


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
    Render the Leadership 200 top section as a PDF.

    Mirrors the front-end logic so that:
      • Left fractions = manual received + auto gifts from the table.
      • Right amounts = total dollars of gifts assigned to that level.
      • Top blue pill = sum of all individual gifts.
    """
    goal = _parse_number(state.get("goal"))
    title = state.get("title", "LEADERSHIP 200")
    rows = state.get("rows", []) or []
    gifts_raw = state.get("gifts", []) or []

    # ----- normalise gifts -------------------------------------------------
    gifts = []
    for g in gifts_raw:
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

    # Grand total for "TOTAL RECEIVED TO DATE"
    total_received = sum(g["amount"] for g in gifts)

    # ----- build rowInfos like JS recalcAll() ------------------------------
    row_infos = []
    for r in rows:
        base = _gift_base(r.get("label", ""))
        row_infos.append({"row": r, "base": base, "gifts": []})

    # assign each gift to a level based on amount
    for gift in gifts:
        amt = gift["amount"]
        for idx, info in enumerate(row_infos):
            base = info["base"] or 0.0
            upper = row_infos[idx - 1]["base"] or float("inf") if idx > 0 else float("inf")
            if amt >= base and amt < upper:
                info["gifts"].append(gift)
                break

    # compute derived fields per row
    for info in row_infos:
        r = info["row"]
        needed = int(_parse_number(r.get("needed")))
        manual_received = int(_parse_number(r.get("received")))
        auto_received = len(info["gifts"])
        info["needed"] = needed
        info["total_received"] = manual_received + auto_received
        info["amount_received"] = sum(g["amount"] for g in info["gifts"])

    # ----- draw PDF --------------------------------------------------------
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)

    # Colors matched to HTML
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

    # HEADER
    y = height - 28
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

    # TOP STRIP
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
    c.drawCentredString(label_center_x, strip_y - 17, _format_currency(total_received))

    # TRIANGLE (narrower, same height)
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
            col = _blend(TRI1, TRI2, (t - 0.55) / 0.27)
        else:
            col = _blend(TRI2, TRI3, (t - 0.82) / 0.18)

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

    # TOTAL inside triangle
    c.setFont("Times-Roman", 9)
    c.setFillColor(colors.black)
    c.drawCentredString(center, tri_top - 8, "TOTAL")
    c.setFont("Times-Bold", 20)
    c.drawCentredString(center, tri_top - 25, _format_currency(goal))

    # BARS
    row_h = 24
    row_gap = 7
    n_rows = len(row_infos)
    step = row_h + row_gap

    bars_top = tri_base_y + tri_height - 55
    bar_left = margin_x
    bar_right = width - margin_x
    bar_width = bar_right - bar_left

    c.setFont("Times-Bold", 12)
    c.setFillColor(BLUE_DARK)
    c.drawString(bar_left, bars_top + 32, "Gifts Received / Needed")
    c.drawRightString(bar_right, bars_top + 32, "Total Gift / Pledge Dollars Committed")

    c.setFont("Times-Roman", 9)
    steps_bar = 140

    for i, info in enumerate(row_infos):
        r = info["row"]
        yb = bars_top - i * step
        is_blue = (i % 2 == 0)
        step_w = bar_width / float(steps_bar)

        for j in range(steps_bar):
            t = j / float(steps_bar - 1)

            if is_blue:
                if t <= 0.18:
                    col = _blend(BLUE, BLUE_MID, t / 0.18)
                elif t <= 0.5:
                    col = _blend(BLUE_MID, WHITE, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = _blend(WHITE, BLUE_MID, (t - 0.5) / 0.32)
                else:
                    col = _blend(BLUE_MID, BLUE, (t - 0.82) / 0.18)
            else:
                if t <= 0.18:
                    col = _blend(RED, RED_LIGHT, t / 0.18)
                elif t <= 0.5:
                    col = _blend(RED_LIGHT, WHITE, (t - 0.18) / 0.32)
                elif t <= 0.82:
                    col = _blend(WHITE, RED_LIGHT, (t - 0.5) / 0.32)
                else:
                    col = _blend(RED_LIGHT, RED, (t - 0.82) / 0.18)

            c.setFillColor(col)
            c.rect(
                bar_left + j * step_w,
                yb,
                step_w + 0.5,
                row_h,
                stroke=0,
                fill=1,
            )

        # left fraction
        c.setFillColor(colors.white)
        c.drawString(
            bar_left + 5,
            yb + 7.5,
            f"{info['total_received']}/{info['needed']}",
        )

        # center label
        c.setFillColor(BLUE_DARK)
        c.drawCentredString(
            bar_left + bar_width / 2.0,
            yb + 7.5,
            r.get("label", ""),
        )

        # right dollars
        c.setFillColor(colors.white)
        c.drawRightString(
            bar_right - 5,
            yb + 7.5,
            _format_currency(info["amount_received"]),
        )

    # BOTTOM BANNER
    banner_w = width * 0.55
    banner_h = 24
    banner_x = (width - banner_w) / 2.0
    banner_y = bars_top - n_rows * step - 16

    c.setFillColor(BLUE_DARK)
    c.rect(banner_x, banner_y, banner_w, banner_h, stroke=0, fill=1)

    total_gifts_needed = sum(int(_parse_number(r.get("needed"))) for r in rows)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 12)
    c.drawCentredString(
        width / 2.0,
        banner_y + 7,
        f"{total_gifts_needed:,} LEADERSHIP GIFTS/PLEDGES",
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
