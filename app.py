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
    """Create leadership_state table and make sure there is one row with default_state()."""
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


app = Flask(__name__)

# Initialize DB once on startup
ensure_table()

from flask import send_from_directory  # noqa: E402


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

    base["goal"] = incoming.get("goal", base["goal"])
    base["title"] = incoming.get("title", base["title"])

    incoming_rows = incoming.get("rows") or []
    for i, row in enumerate(base["rows"]):
        if i < len(incoming_rows):
            inc = incoming_rows[i] or {}
            if "received" in inc:
                row["received"] = inc["received"]

    base["gifts"] = incoming.get("gifts", base["gifts"])
    return base


@app.route("/api/state", methods=["POST"])
def save_state():
    try:
        incoming = request.get_json()
        merged_state = merge_state_with_template(incoming)

        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT id FROM leadership_state ORDER BY id LIMIT 1;")
        row = cur.fetchone()

        if row is None:
            cur.execute(
                "INSERT INTO leadership_state (state_json) VALUES (%s);",
                (json.dumps(merged_state),),
            )
        else:
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


# -------------------------------------------------------------------
# PDF HELPERS
# -------------------------------------------------------------------

def _parse_number(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _gift_base_from_label(label):
    """Extract the base gift amount from a row label like '1 Gift of $25,000,000'."""
    if not label:
        return 0.0
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", label)
    if not m:
        return 0.0
    return _parse_number(m.group(1))


def _blend(c1, c2, t):
    """Linear blend of two reportlab Color objects."""
    t = max(0.0, min(1.0, float(t)))
    r = c1.red + (c2.red - c1.red) * t
    g = c1.green + (c2.green - c1.green) * t
    b = c1.blue + (c2.blue - c1.blue) * t
    return colors.Color(r, g, b)


def _bar_color_at(t, is_blue, BLUE, BLUE_MID, RED, RED_LIGHT):
    """
    New gradient: removes white completely.
    Now matches the HTML bar gradients:

    Blue:
      #0047b5 (blue-dark)
      → #4f7fd6 (blue-mid)
      → #8fb3ea (blue-light)
      → #4f7fd6 (blue-mid)
      → #0047b5 (blue-dark)

    Red:
      #c6001a (red-dark)
      → #ff7a7a (red-mid)
      → #ffb3b3 (red-light)
      → #ff7a7a (red-mid)
      → #c6001a (red-dark)
    """

    # Clamp
    t = max(0.0, min(1.0, float(t)))

    if is_blue:
        BLUE_LIGHT = colors.HexColor("#8fb3ea")

        if t <= 0.25:
            # dark → mid
            return _blend(BLUE, BLUE_MID, t / 0.25)
        elif t <= 0.50:
            # mid → light
            return _blend(BLUE_MID, BLUE_LIGHT, (t - 0.25) / 0.25)
        elif t <= 0.75:
            # light → mid
            return _blend(BLUE_LIGHT, BLUE_MID, (t - 0.50) / 0.25)
        else:
            # mid → dark
            return _blend(BLUE_MID, BLUE, (t - 0.75) / 0.25)

    else:
        RED_LIGHTER = colors.HexColor("#ffb3b3")

        if t <= 0.25:
            # red-dark → red-mid
            return _blend(RED, RED_LIGHT, t / 0.25)
        elif t <= 0.50:
            # red-mid → red-light
            return _blend(RED_LIGHT, RED_LIGHTER, (t - 0.25) / 0.25)
        elif t <= 0.75:
            # red-light → red-mid
            return _blend(RED_LIGHTER, RED_LIGHT, (t - 0.50) / 0.25)
        else:
            # red-mid → red-dark
            return _blend(RED_LIGHT, RED, (t - 0.75) / 0.25)


def _draw_bar_gradient(c, x, y, w, h, is_blue, BLUE, BLUE_MID, RED, RED_LIGHT, steps=140):
    """Draw a horizontal gradient bar, matching the HTML look."""
    step_w = w / float(steps)
    for i in range(steps):
        t = i / float(steps - 1)
        col = _bar_color_at(t, is_blue, BLUE, BLUE_MID, RED, RED_LIGHT)
        c.setFillColor(col)
        c.rect(x + i * step_w, y, step_w + 0.5, h, stroke=0, fill=1)


def _triangle_color_at(t, TRI_LIGHT, TRI_LIGHT2, TRI_LIGHT3):
    """Soft vertical gradient: #d5ecff -> #e3f3ff -> #edf8ff."""
    t = max(0.0, min(1.0, float(t)))
    if t <= 0.55:
        return TRI_LIGHT
    elif t <= 0.82:
        return _blend(TRI_LIGHT, TRI_LIGHT2, (t - 0.55) / (0.82 - 0.55))
    else:
        return _blend(TRI_LIGHT2, TRI_LIGHT3, (t - 0.82) / (1.0 - 0.82))


def _draw_triangle_gradient(c, width, top_y, height_px, TRI_LIGHT, TRI_LIGHT2, TRI_LIGHT3, steps=220):
    """Draw the large background triangle with a soft vertical gradient."""
    base_y = top_y - height_px
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
        amt = _parse_number(g.get("amount"))
        if amt > 0:
            gifts.append(amt)
    gifts.sort(reverse=True)
    total_received_to_date = sum(gifts)

    # Row info
    row_infos = []
    for r in rows_raw:
        base = _gift_base_from_label(r.get("label", ""))
        row_infos.append(
            {
                "label": r.get("label", ""),
                "received": int(_parse_number(r.get("received", 0))),
                "needed": int(_parse_number(r.get("needed", 0))),
                "base": base,
                "gifts": [],
            }
        )

    # Highest dollar row first
    row_infos.sort(key=lambda ri: ri["base"], reverse=True)

    # Bucket gifts into rows based on ranges
    for g_amt in gifts:
        for i, ri in enumerate(row_infos):
            base = ri["base"] or 0
            upper = row_infos[i - 1]["base"] if i > 0 else float("inf")
            if g_amt >= base and g_amt < upper:
                ri["gifts"].append(g_amt)
                break

    for ri in row_infos:
        ri["auto_total"] = sum(ri["gifts"])
        auto_count = len(ri["gifts"])
        if ri["received"] <= 0 and auto_count > 0:
            ri["received"] = auto_count

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    width, height = landscape(letter)

    # Colors from HTML
    BLUE = colors.HexColor("#0047b5")
    BLUE_DARK = colors.HexColor("#00308b")
    BLUE_MID = colors.HexColor("#4f7fd6")
    RED = colors.HexColor("#c6001a")
    RED_LIGHT = colors.HexColor("#ff7a7a")
    TRI_LIGHT = colors.HexColor("#d5ecff")
    TRI_LIGHT2 = colors.HexColor("#e3f3ff")
    TRI_LIGHT3 = colors.HexColor("#edf8ff")
    BEIGE = colors.HexColor("#f3ede5")

    margin_x = 50
    header_center_x = width / 2.0

    # Header stack
    y = height - 36
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 8)
    c.drawCentredString(header_center_x, y, "TURNING POINT WITH DR. DAVID JEREMIAH")

    y -= 20
    c.setFont("Times-Bold", 26)
    c.setFillColor(colors.HexColor("#9f1515"))
    c.drawCentredString(header_center_x, y, title.upper())

    y -= 16
    c.setFont("Times-Roman", 9)
    c.setFillColor(colors.black)
    c.drawCentredString(header_center_x, y, "ACCELERATE YOUR VISION")

    y -= 14
    c.setFont("Times-Italic", 7.5)
    c.drawCentredString(
        header_center_x,
        y,
        "Delivering the Unchanging Word of God to an Ever-Changing World",
    )

    # Top beige strip + received pill
    strip_top = y - 8
    strip_h = 24
    c.setFillColor(BEIGE)
    c.rect(0, strip_top - strip_h, width, strip_h, stroke=0, fill=1)

    label_x = width - margin_x - 170
    c.setFont("Times-Roman", 7)
    c.setFillColor(colors.black)
    c.drawString(label_x, strip_top - 4, "TOTAL RECEIVED TO DATE")

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

    # Total text inside triangle
    c.setFillColor(colors.black)
    c.setFont("Times-Roman", 8)
    c.drawCentredString(width / 2.0, tri_top_y - 26, "TOTAL")
    c.setFont("Times-Bold", 18)
    c.drawCentredString(width / 2.0, tri_top_y - 46, "${:,.0f}".format(goal))

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
    for idx, ri in enumerate(row_infos):
        y_bar = bars_top_y - idx * (row_h + row_gap)
        is_blue = (idx % 2 == 0)

        _draw_bar_gradient(
            c,
            bar_left,
            y_bar,
            bar_width,
            row_h,
            is_blue,
            BLUE,
            BLUE_MID,
            RED,
            RED_LIGHT,
        )

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
    last_row_y = bars_top_y - (len(row_infos) - 1) * (row_h + row_gap)
    banner_y = last_row_y - 40
    banner_x = (width - banner_w) / 2.0

    c.setFillColor(BLUE_DARK)
    c.rect(banner_x, banner_y, banner_w, banner_h, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Times-Bold", 11)
    total_gifts = sum(ri.get("needed", 0) for ri in row_infos)
    banner_text = f"{total_gifts:,} LEADERSHIP GIFTS/PLEDGES"
    c.drawCentredString(banner_x + banner_w / 2.0, banner_y + 6, banner_text)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


# -------------------------------------------------------------------
# PDF ROUTES
# -------------------------------------------------------------------

@app.route("/api/generate-pdf", methods=["POST"])
def api_generate_pdf():
    """
    POSTed from the front-end with the current state JSON.
    Returns a PDF stream.
    """
    state = request.get_json(force=True) or {}
    pdf_buf = _build_pdf_from_state(state)
    return send_file(
        pdf_buf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="Leadership200.pdf",
    )


@app.route("/generate-pdf", methods=["GET"])
def generate_pdf_from_saved_state():
    """
    Convenience GET route (for when you hit /generate-pdf directly).
    Uses the state stored in the database.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT state_json FROM leadership_state ORDER BY id LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and row[0]:
        if isinstance(row[0], (dict, list)):
            state = row[0]
        else:
            state = json.loads(row[0])
    else:
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
    app.run(host="0.0.0.0", port=5000, debug=True)
