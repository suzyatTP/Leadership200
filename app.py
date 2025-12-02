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


def extract_amount(label):
    """Extracts dollar value from label string like '1 Gift of $25,000,000'."""
    if not label:
        return 0
    m = re.search(r"\$([\d,]+)", label or "")
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
    """Ensures the DB table exists."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leadership200_state (
            id SERIAL PRIMARY KEY,
            singleton_key BOOLEAN UNIQUE DEFAULT TRUE,
            payload JSONB NOT NULL
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def load_state():
    """Loads state JSON from DB, or returns default_state if none."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT payload FROM leadership200_state WHERE singleton_key = TRUE")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return default_state()

    try:
        payload = row[0]
        if not isinstance(payload, dict):
            return default_state()
        return payload
    except Exception:
        return default_state()


def save_state(state):
    """Upserts state into DB."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO leadership200_state (singleton_key, payload)
        VALUES (TRUE, %s)
        ON CONFLICT (singleton_key)
        DO UPDATE SET payload = EXCLUDED.payload
        """,
        (json.dumps(state),),
    )
    conn.commit()
    cur.close()
    conn.close()


# -------------------------------------------------------------------
# PDF HELPER FUNCTIONS
# -------------------------------------------------------------------

BLUE = colors.HexColor("#0047b5")
BLUE_LIGHT = colors.HexColor("#4f7fd6")
BLUE_DARK = colors.HexColor("#00308b")
RED = colors.HexColor("#c6001a")
RED_LIGHT = colors.HexColor("#ff7a7a")
TRI_LIGHT = colors.HexColor("#d5ecff")
TRI_LIGHT2 = colors.HexColor("#e3f3ff")
TRI_LIGHT3 = colors.HexColor("#edf8ff")


def _parse_number(raw):
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw)
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _triangle_color_at(t, c1, c2, c3):
    """Simple 3-stop gradient interpolation for the triangle."""
    # Clamp
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        r = t / 0.5
        a, b = c1, c2
    else:
        r = (t - 0.5) / 0.5
        a, b = c2, c3

    def lerp(x, y, r_):
        return x + (y - x) * r_

    return colors.Color(
        lerp(a.red, b.red, r),
        lerp(a.green, b.green, r),
        lerp(a.blue, b.blue, r),
    )


def _draw_triangle_gradient(c, width, top_y, height, TRI_LIGHT, TRI_LIGHT2, TRI_LIGHT3):
    """Draws the big light-blue triangle background."""
    base_y = top_y - height

    steps = 60
    c.saveState()
    path = c.beginPath()
    path.moveTo(width / 2.0, top_y)
    path.lineTo(width, base_y)
    path.lineTo(0, base_y)
    path.close()
    c.clipPath(path, stroke=0, fill=0)

    for i in range(steps):
        tt = i / float(steps - 1)
        col = _triangle_color_at(tt, TRI_LIGHT, TRI_LIGHT2, TRI_LIGHT3)
        band_y = base_y + (top_y - base_y) * tt
        band_h = (top_y - base_y) / float(steps)
        c.setFillColor(col)
        c.rect(0, band_y, width, band_h + 1, stroke=0, fill=1)

    c.restoreState()


def _build_pdf_from_state(state):
    """Core function: builds the Leadership 200 top-section PDF and returns a BytesIO buffer."""
    goal = _parse_number(state.get("goal", 0))
    title = state.get("title") or "LEADERSHIP 200"
    rows_raw = state.get("rows", []) or []
    gifts_raw = state.get("gifts", []) or []

    # Normalize gifts
    gifts = []
    for g in gifts_raw:
        try:
            amt = _parse_number(g.get("amount", 0))
            gifts.append(
                {
                    "donorName": g.get("donorName", "") or "",
                    "idNumber": g.get("idNumber", "") or "",
                    "amount": amt,
                    "purpose": g.get("purpose", "") or "",
                }
            )
        except Exception:
            continue

    gifts.sort(key=lambda g: g["amount"], reverse=True)

    # Rows
    row_infos = []
    for r in rows_raw:
        label = (r.get("label") or "").strip()
        received = int(_parse_number(r.get("received", 0)))
        needed = int(_parse_number(r.get("needed", 0)))
        base_amount = extract_amount(label)
        row_infos.append(
            {
                "label": label,
                "received": max(received, 0),
                "needed": max(needed, 0),
                "base_amount": float(base_amount),
            }
        )

    # Assign gifts to rows by amount ranges (descending)
    row_infos_sorted = sorted(row_infos, key=lambda x: x["base_amount"], reverse=True)
    for ri in row_infos_sorted:
        ri["auto_gifts"] = []
        ri["auto_total"] = 0.0

    for g in gifts:
        amt = g["amount"]
        for idx, ri in enumerate(row_infos_sorted):
            base = ri["base_amount"] or 0.0
            if idx == 0:
                upper = float("inf")
            else:
                upper = row_infos_sorted[idx - 1]["base_amount"] or float("inf")
            if amt >= base and amt < upper:
                ri["auto_gifts"].append(g)
                ri["auto_total"] += amt
                break

    # Calculate totals
    DIRECT_MAIL_TOTAL = 3000000
    total_planned_levels = 0.0
    total_needed = 0

    for ri in row_infos_sorted:
        level_goal = ri["base_amount"] * ri["needed"]
        total_planned_levels += level_goal
        total_needed += ri["needed"]

    planned_with_direct = total_planned_levels + DIRECT_MAIL_TOTAL
    total_received_to_date = sum(g["amount"] for g in gifts)

    # Build PDF
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)

    margin_x = 60
    header_h = 80
    strip_h = 22

    # Header
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 7)
    c.drawCentredString(
        width / 2.0,
        height - 24,
        "TURNING POINT WITH DR. DAVID JEREMIAH",
    )

    c.setFont("Times-Bold", 30)
    c.setFillColor(colors.HexColor("#9f1515"))
    c.drawCentredString(width / 2.0, height - 52, title.upper())

    c.setFont("Times-Roman", 11)
    c.setFillColor(colors.black)
    c.drawCentredString(width / 2.0, height - 70, "ACCELERATE YOUR VISION")

    c.setFont("Times-Italic", 8)
    c.drawCentredString(
        width / 2.0,
        height - 83,
        "Delivering the Unchanging Word of God to an Ever-Changing World",
    )

    # Top beige strip
    strip_top = height - header_h - 10
    c.setFillColor(colors.HexColor("#f3ede5"))
    c.rect(0, strip_top - strip_h, width, strip_h, stroke=0, fill=1)

    # "TOTAL RECEIVED TO DATE" label + blue pill
    label_x = width - margin_x - 170
    # Slightly larger and nudged to sit clearly above the blue total box
    c.setFont("Times-Roman", 8)
    c.setFillColor(colors.black)
    c.drawString(label_x, strip_top - 5, "TOTAL RECEIVED TO DATE")

    pill_w = 170
    pill_h = 19
    pill_x = width - margin_x - pill_w
    pill_y = strip_top - strip_h + 3
    c.setFillColor(BLUE_DARK)
    c.roundRect(pill_x, pill_y, pill_w, pill_h, 3, stroke=0, fill=1)

    c.setFont("Times-Bold", 10)
    c.setFillColor(colors.white)
    c.drawCentredString(
        pill_x + pill_w / 2.0,
        pill_y + 5.5,
        "${:,.0f}".format(total_received_to_date),
    )

    # Triangle
    tri_top_y = strip_top - 48
    tri_height = 260
    _draw_triangle_gradient(
        c,
        width,
        tri_top_y,
        tri_height,
        TRI_LIGHT,
        TRI_LIGHT2,
        TRI_LIGHT3,
    )

    # Total text inside triangle – moved slightly higher and enlarged for readability
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(width / 2.0, tri_top_y - 20, "TOTAL")
    c.setFont("Times-Bold", 20)
    c.drawCentredString(width / 2.0, tri_top_y - 38, "${:,.0f}".format(goal))

    # Bars area
    bars_area_height = tri_height * 0.60
    bars_top_y = (tri_top_y - tri_height) + bars_area_height + 10
    row_h = 17
    row_gap = 5

    labels_y = bars_top_y + row_h + 8
    c.setFont("Times-Italic", 7)
    c.setFillColor(colors.black)
    c.drawString(margin_x, labels_y, "Gifts Received/Needed")
    c.drawRightString(
        width - margin_x, labels_y, "Total Gift/Pledge Dollars Committed"
    )

    bar_left = margin_x
    bar_right = width - margin_x
    bar_width = bar_right - bar_left

    # Draw each bar row
    c.setFont("Times-Roman", 7.5)
    for idx, ri in enumerate(row_infos_sorted):
        y_bar = bars_top_y - idx * (row_h + row_gap)
        is_blue = (idx % 2 == 0)

        # gradient background
        if is_blue:
            c.saveState()
            c.setFillColor(BLUE)
            c.setStrokeColor(colors.white)
            c.rect(bar_left, y_bar, bar_width, row_h, stroke=0, fill=1)
            c.restoreState()

            c.saveState()
            c.clipRect(bar_left, y_bar, bar_width, row_h)
            for i in range(50):
                t = i / 49.0
                col = colors.Color(
                    BLUE.red + (BLUE_LIGHT.red - BLUE.red) * t,
                    BLUE.green + (BLUE_LIGHT.green - BLUE.green) * t,
                    BLUE.blue + (BLUE_LIGHT.blue - BLUE.blue) * t,
                )
                x = bar_left + bar_width * t
                c.setFillColor(col)
                c.rect(x, y_bar, bar_width / 50.0 + 1, row_h, stroke=0, fill=1)
            c.restoreState()
        else:
            c.saveState()
            c.setFillColor(RED)
            c.setStrokeColor(colors.white)
            c.rect(bar_left, y_bar, bar_width, row_h, stroke=0, fill=1)
            c.restoreState()

            c.saveState()
            c.clipRect(bar_left, y_bar, bar_width, row_h)
            for i in range(50):
                t = i / 49.0
                col = colors.Color(
                    RED.red + (RED_LIGHT.red - RED.red) * t,
                    RED.green + (RED_LIGHT.green - RED.green) * t,
                    RED.blue + (RED_LIGHT.blue - RED.blue) * t,
                )
                x = bar_left + bar_width * t
                c.setFillColor(col)
                c.rect(x, y_bar, bar_width / 50.0 + 1, row_h, stroke=0, fill=1)
            c.restoreState()

        # left fraction
        c.setFillColor(colors.white)
        if ri["needed"]:
            frac_text = f"{ri['received']}/{ri['needed']}"
        else:
            frac_text = f"{ri['received']}"
        c.drawString(bar_left + 5, y_bar + 5, frac_text)

        # center label
        c.setFillColor(colors.white)
        c.drawCentredString(bar_left + bar_width / 2.0, y_bar + 5, ri["label"])

        # right committed
        committed = ri.get("auto_total", 0.0)
        c.drawRightString(
            bar_right - 6,
            y_bar + 5,
            "${:,.0f}".format(committed),
        )

    # Bottom banner
    banner_w = width * 0.55
    banner_h = 22
    last_row_y = bars_top_y - (len(row_infos_sorted) - 1) * (row_h + row_gap)
    # Nudge the bottom banner slightly closer to the bars
    banner_y = last_row_y - 32
    banner_x = (width - banner_w) / 2.0

    c.setFillColor(BLUE_DARK)
    c.rect(banner_x, banner_y, banner_w, banner_h, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 11)
    total_gifts = sum(ri.get("needed", 0) for ri in row_infos_sorted)
    banner_text = f"{total_gifts:,} LEADERSHIP GIFTS/PLEDGES"
    c.drawCentredString(banner_x + banner_w / 2.0, banner_y + 6, banner_text)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


# -------------------------------------------------------------------
# FLASK APP
# -------------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/state", methods=["GET"])
def api_get_state():
    try:
        state = load_state()
    except Exception:
        state = default_state()
    return jsonify(state)


@app.route("/api/state", methods=["POST"])
def api_set_state():
    data = request.get_json(force=True, silent=True) or {}
    try:
        save_state(data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/generate-pdf")
def generate_pdf():
    try:
        state = load_state()
    except Exception:
        state = default_state()

    pdf_buf = _build_pdf_from_state(state)
    return send_file(
        pdf_buf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="Leadership200.pdf",
    )


# -------------------------------------------------------------------

if __name__ == "__main__":
    ensure_table()
    app.run(host="0.0.0.0", port=5000, debug=True)
