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


# Serve index.html from the project root
app = Flask(__name__)

# Initialize DB
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


# ====== PDF GENERATION ROUTE =======================================
from reportlab.lib.pagesizes import letter, landscape  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402


def draw_gradient_bar(c, x, y, w, h, palette, steps=120):
    """
    Draw a horizontal gradient bar using multiple thin rectangles.

    palette = [(pos0, (r,g,b)), (pos1, (r,g,b)), ...] where pos in [0,1].
    """
    for i in range(steps):
        t = i / float(steps)
        # find segment of palette that contains t
        r = g = b = 0.0
        for j in range(len(palette) - 1):
            p0, col0 = palette[j]
            p1, col1 = palette[j + 1]
            if t >= p0 and t <= p1:
                if p1 == p0:
                    u = 0.0
                else:
                    u = (t - p0) / (p1 - p0)
                r = col0[0] + (col1[0] - col0[0]) * u
                g = col0[1] + (col1[1] - col0[1]) * u
                b = col0[2] + (col1[2] - col0[2]) * u
                break
        else:
            # fallback to last color
            r, g, b = palette[-1][1]

        c.setFillColorRGB(r, g, b)
        x0 = x + (w * i / float(steps))
        c.rect(x0, y, w / float(steps) + 0.5, h, fill=1, stroke=0)


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

    # Brand colors (synced with CSS) :contentReference[oaicite:1]{index=1}
    BLUE = (0 / 255.0, 71 / 255.0, 181 / 255.0)  # #0047b5
    BLUE_MID = (79 / 255.0, 127 / 255.0, 214 / 255.0)  # #4f7fd6
    BLUE_DARK = (0 / 255.0, 48 / 255.0, 139 / 255.0)  # #00308b
    RED = (198 / 255.0, 0 / 255.0, 26 / 255.0)  # #c6001a
    RED_MID = (255 / 255.0, 122 / 255.0, 122 / 255.0)  # #ff7a7a
    HEADER_RED = (159 / 255.0, 21 / 255.0, 21 / 255.0)  # #9f1515
    TRI_LIGHT = (213 / 255.0, 236 / 255.0, 255 / 255.0)  # #d5ecff
    TOP_BEIGE = (0xFB / 255.0, 0xF7 / 255.0, 0xF2 / 255.0)  # #fbf7f2

    BLUE_PALETTE = [
        (0.0, BLUE),
        (0.18, BLUE_MID),
        (0.50, (1.0, 1.0, 1.0)),
        (0.82, BLUE_MID),
        (1.0, BLUE),
    ]
    RED_PALETTE = [
        (0.0, RED),
        (0.18, RED_MID),
        (0.50, (1.0, 1.0, 1.0)),
        (0.82, RED_MID),
        (1.0, RED),
    ]

    # ---------- TOTALS (including fixed $3M direct mail) ----------
    DIRECT_MAIL = 3_000_000
    gifts_total = sum(g["amount"] for g in gifts)

    planned_total_levels = 0
    for r in rows:
        gift_value = extract_amount(r["label"])
        planned_total_levels += gift_value * r["needed"]
    triangle_total = planned_total_levels + DIRECT_MAIL
    total_needed = sum(r["needed"] for r in rows)

    # ---------- HEADER ----------
    c.setFillColorRGB(0.27, 0.27, 0.27)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(
        width / 2, top_margin + 24, "TURNING POINT WITH DR. DAVID JEREMIAH"
    )

    c.setFillColorRGB(*HEADER_RED)
    c.setFont("Times-Bold", 30)
    c.drawCentredString(width / 2, top_margin, title)

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 10)
    c.drawCentredString(width / 2, top_margin - 18, "ACCELERATE YOUR VISION")

    c.setFont("Times-Italic", 8.5)
    c.drawCentredString(
        width / 2,
        top_margin - 32,
        "Delivering the Unchanging Word of God to an Ever-Changing World",
    )

    # ---------- TOP STRIP / PILL ----------
    strip_top = top_margin - 46
    strip_height = 26
    c.setFillColorRGB(*TOP_BEIGE)
    c.rect(0, strip_top, width, strip_height, fill=1, stroke=0)

    pill_width = 160
    pill_height = 24
    pill_right = width - right_margin
    pill_left = pill_right - pill_width
    pill_bottom = strip_top + (strip_height - pill_height) / 2
    pill_top = pill_bottom + pill_height

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 8)
    c.drawRightString(pill_right, pill_top + 6, "TOTAL RECEIVED TO DATE")

    c.setFillColorRGB(*BLUE_DARK)
    c.roundRect(pill_left, pill_bottom, pill_width, pill_height, 4, fill=1, stroke=0)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Times-Bold", 12)
    c.drawCentredString(
        (pill_left + pill_right) / 2,
        pill_bottom + 7,
        "${:,}".format(int(gifts_total)),
    )

    # ---------- TRIANGLE BACKGROUND ----------
    tri_top_y = strip_top - 40
    tri_base_y = tri_top_y - 190  # vertical size of triangle

    path = c.beginPath()
    path.moveTo(width / 2, tri_top_y)  # top
    path.lineTo(width * 0.06, tri_base_y)  # bottom left
    path.lineTo(width * 0.94, tri_base_y)  # bottom right
    path.close()

    c.setFillColorRGB(*TRI_LIGHT)
    c.drawPath(path, fill=1, stroke=0)

    # TOTAL text inside triangle
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Roman", 9)
    c.drawCentredString(width / 2, tri_top_y - 32, "TOTAL")

    c.setFont("Times-Bold", 20)
    c.drawCentredString(
        width / 2,
        tri_top_y - 52,
        "${:,}".format(int(triangle_total)),
    )

    # ---------- ROW LABELS & BARS (NOW INSIDE TRIANGLE) ----------
    # move bars up so they sit on top of the triangle instead of below it
    bars_top_y = tri_base_y + 16  # was tri_base_y - 16

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Times-Italic", 7)
    c.drawString(left_margin, bars_top_y + 20, "Gifts Received/Needed")
    c.drawRightString(
        width - right_margin,
        bars_top_y + 20,
        "Total Gift/Pledge Dollars Committed",
    )

    bar_left = left_margin
    bar_right = width - right_margin
    bar_height = 16
    bar_gap = 6

    c.setFont("Times-Roman", 7.5)
    y = bars_top_y

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

        level_sum = sum(
            g["amount"]
            for g in gifts
            if base_amt <= g["amount"] < upper_amt
        )

        # draw gradient bar
        if idx % 2 == 0:
            draw_gradient_bar(
                c,
                bar_left,
                y,
                bar_right - bar_left,
                bar_height,
                BLUE_PALETTE,
            )
        else:
            draw_gradient_bar(
                c,
                bar_left,
                y,
                bar_right - bar_left,
                bar_height,
                RED_PALETTE,
            )

        # overlay white text
        c.setFillColorRGB(1, 1, 1)

        # left: received/needed
        c.drawString(bar_left + 4, y + 4, f"{rec}/{needed}")

        # center: label
        c.drawCentredString((bar_left + bar_right) / 2, y + 4, label)

        # right: amount
        c.drawRightString(
            bar_right - 4,
            y + 4,
            "${:,}".format(int(level_sum)),
        )

        y -= bar_height + bar_gap

    # ---------- BOTTOM BLUE BANNER ----------
    banner_height = 20
    banner_width = 360
    banner_y = y - 26
    banner_x = (width - banner_width) / 2

    c.setFillColorRGB(*BLUE_DARK)
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

    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="Leadership200.pdf",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
