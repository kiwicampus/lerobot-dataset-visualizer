"""
FOMO Episodes Explorer – Interactive Map Visualization Tool  (v2)
=================================================================

Launch:
    python explorer_app.py

Then open http://localhost:5050 in your browser.

Dependencies (in addition to those in standalone_bq_curated_fomo.py):
    pip install flask
"""

import csv as _csv
import json
import re
from datetime import datetime, date

from flask import Flask, jsonify, request, render_template_string

from standalone_bq_fomo import BigQueryClient, FULL_TABLE_ID
from download_episode_rosbag import find_rosbag, DEFAULT_CSV

# ── Config ──────────────────────────────────────────────────────────────────
PORT = 5050
FOMO_USBS_TABLE = "`autonomy-286821.data_capture.fomo_usbs`"

app = Flask(__name__)
_client = None
_filters_cache = None


def get_client():
    global _client
    if _client is None:
        _client = BigQueryClient()
    return _client


# ── Helpers ─────────────────────────────────────────────────────────────────

def parse_wkt_point(wkt):
    """Parse WKT POINT to [lat, lng] for Leaflet."""
    if wkt is None:
        return None
    m = re.match(r"POINT\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)", str(wkt))
    if m:
        return [float(m.group(2)), float(m.group(1))]  # [lat, lng]
    return None


def make_serializable(value):
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [make_serializable(i) for i in value]
    return str(value)


def serialize_episode(ep):
    result = {}
    for k, v in ep.items():
        if k == "gps_path":
            pts = [parse_wkt_point(p) for p in (v or [])]
            result[k] = [p for p in pts if p is not None]
        elif k in ("initial_gps_point", "final_gps_point"):
            result[k] = parse_wkt_point(v)
        else:
            result[k] = make_serializable(v)
    # Derived: bucket URL
    if result.get("mcap_path"):
        gs = result["mcap_path"]
        if gs.startswith("gs://"):
            folder = "/".join(gs[5:].split("/")[:-1])
            result["bucket_url"] = (
                f"https://console.cloud.google.com/storage/browser/{folder}"
            )
    elif result.get("rosbag_name"):
        name = result["rosbag_name"].split("/")[-1]
        result["bucket_url"] = (
            f"https://console.cloud.google.com/storage/browser/"
            f"autonomy-vision/rosbags/fomo/{name}/"
        )
    return result


# ── API Routes ──────────────────────────────────────────────────────────────

@app.route("/api/episodes")
def api_episodes():
    bq = get_client()
    query = f"SELECT * FROM {FULL_TABLE_ID}"
    conditions = []

    # Multi-value filters: accept comma-separated values
    allowed = {
        "robot_id", "fleet_name", "way_type", "surface",
        "weather", "time_of_the_day", "project",
    }
    for param in allowed:
        val = request.args.get(param, "").strip()
        if val and val != "all":
            values = [v.strip().replace("'", "''") for v in val.split(",") if v.strip()]
            if len(values) == 1:
                conditions.append(f"{param} = '{values[0]}'")
            elif len(values) > 1:
                in_clause = ",".join(f"'{v}'" for v in values)
                conditions.append(f"{param} IN ({in_clause})")

    # Date range filter
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    if date_from:
        conditions.append(f"timestamp >= '{date_from}'")
    if date_to:
        conditions.append(f"timestamp <= '{date_to}T23:59:59'")

    # Rosbag name: substring match (case-insensitive); STRPOS avoids LIKE wildcards (% _)
    rosbag_q = request.args.get("rosbag_name", "").strip()
    if rosbag_q:
        safe = rosbag_q.replace("'", "''")
        conditions.append(
            "STRPOS(LOWER(IFNULL(CAST(rosbag_name AS STRING), '')), "
            f"LOWER('{safe}')) > 0"
        )

    # Bounding box filter for area selection
    bbox = request.args.get("bbox", "").strip()
    if bbox:
        try:
            parts = [float(x) for x in bbox.split(",")]
            if len(parts) == 4:
                south, west, north, east = parts
                # Filter using initial_gps_point geography
                conditions.append(
                    f"ST_WITHIN(initial_gps_point, "
                    f"ST_GEOGFROMTEXT('POLYGON(("
                    f"{west} {south},{east} {south},"
                    f"{east} {north},{west} {north},"
                    f"{west} {south}))'))"
                )
        except ValueError:
            pass

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    limit = min(int(request.args.get("limit", 1500)), 5000)
    query += f" ORDER BY timestamp DESC LIMIT {limit}"

    rows = bq.run_query(query)
    return jsonify([serialize_episode(r) for r in rows])


@app.route("/api/filters")
def api_filters():
    global _filters_cache
    if _filters_cache is not None:
        return jsonify(_filters_cache)

    bq = get_client()
    data = {}
    for col in [
        "robot_id", "fleet_name", "way_type", "surface",
        "weather", "time_of_the_day", "project",
    ]:
        data[col] = bq.get_distinct_values(col)
    data["total_count"] = bq.count_episodes()
    _filters_cache = data
    return jsonify(data)


@app.route("/api/rosbag_names")
def api_rosbag_names():
    """Distinct rosbag_name values from curated_fomo_episodes (for filter autocomplete)."""
    bq = get_client()
    limit = min(int(request.args.get("limit", 3000)), 10_000)
    names = bq.get_distinct_rosbag_names(limit=limit)
    return jsonify({"names": names})


@app.route("/api/lookup_episode")
def api_lookup_episode():
    dataset_index_str = request.args.get("dataset_index", "").strip()
    if not dataset_index_str:
        return jsonify({"error": "dataset_index required"}), 400
    try:
        dataset_index = int(dataset_index_str)
    except ValueError:
        return jsonify({"error": "dataset_index must be an integer"}), 400

    try:
        rosbag_name = find_rosbag(dataset_index, DEFAULT_CSV)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    with open(DEFAULT_CSV, newline="") as f:
        for row in _csv.DictReader(f):
            if int(row["dataset_episode_index"]) == dataset_index:
                return jsonify({
                    "rosbag_name": rosbag_name,
                    "episode_index": int(row["split_index"]),
                })

    return jsonify({"error": "split_index not found"}), 404


@app.route("/api/usbs")
def api_usbs():
    """Latest position per bot from fomo_usbs."""
    bq = get_client()
    limit = min(int(request.args.get("limit", 2000)), 8000)
    query = f"""
        SELECT * FROM {FOMO_USBS_TABLE}
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT {limit}
    """
    rows = bq.run_query(query)
    seen = {}
    for r in rows:
        bid = r.get("bot_id")
        if bid not in seen:
            seen[bid] = {
                "bot_id": bid,
                "lat": r["latitude"],
                "lng": r["longitude"],
                "campus_name": r.get("campus_name"),
                "usb_uuid": r.get("usb_uuid"),
                "usb_size_gb": r.get("usb_size_gb"),
                "usb_used_gb": r.get("usb_used_gb"),
                "usb_format": r.get("usb_format"),
                "timestamp": make_serializable(r["timestamp"]),
            }
    return jsonify(list(seen.values()))


# ── Main page ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


# ── HTML ────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FOMO Episodes Explorer</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;height:100vh;display:flex;overflow:hidden;background:#0f172a}

/* ── Sidebar ── */
.sidebar{width:370px;background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;overflow-y:auto;border-right:1px solid #1e293b;z-index:500}
.sidebar-header{padding:16px 20px 12px;background:linear-gradient(135deg,#1e293b,#0f172a);border-bottom:1px solid #334155}
.sidebar-header h1{font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px}
.sidebar-header p{font-size:11px;color:#64748b;margin-top:3px}

.stats-row{display:flex;gap:8px;padding:10px 16px}
.stat-card{flex:1;background:#1e293b;border-radius:8px;padding:8px 10px;text-align:center}
.stat-num{font-size:20px;font-weight:700;color:#3b82f6}
.stat-label{font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}

.section{padding:4px 16px 10px}
.section-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#475569;margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid #1e293b}

/* ── Multi-select dropdown ── */
.ms-wrap{margin-bottom:8px;position:relative}
.ms-wrap label{display:block;font-size:11px;font-weight:500;color:#94a3b8;margin-bottom:3px}
.ms-btn{width:100%;padding:6px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:12px;cursor:pointer;text-align:left;display:flex;justify-content:space-between;align-items:center;transition:border .2s}
.ms-btn:hover,.ms-btn.open{border-color:#3b82f6}
.ms-btn .arrow{font-size:10px;color:#64748b;transition:transform .2s}
.ms-btn.open .arrow{transform:rotate(180deg)}
.ms-dd{display:none;position:absolute;top:100%;left:0;right:0;background:#1e293b;border:1px solid #334155;border-radius:6px;max-height:180px;overflow-y:auto;z-index:600;margin-top:2px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.ms-dd.show{display:block}
.ms-dd label{display:flex;align-items:center;gap:8px;padding:5px 10px;font-size:12px;color:#cbd5e1;cursor:pointer;transition:background .15s}
.ms-dd label:hover{background:#334155}
.ms-dd input[type=checkbox]{accent-color:#3b82f6;flex-shrink:0}
.ms-dd .ms-actions{display:flex;gap:4px;padding:6px 8px;border-bottom:1px solid #334155}
.ms-dd .ms-actions button{flex:1;padding:4px;background:#0f172a;border:1px solid #334155;border-radius:4px;color:#94a3b8;font-size:10px;cursor:pointer}
.ms-dd .ms-actions button:hover{background:#334155;color:#fff}
.ms-tag-row{display:flex;flex-wrap:wrap;gap:3px;margin-top:4px}
.ms-tag{background:#334155;color:#cbd5e1;font-size:10px;padding:2px 6px;border-radius:4px;display:flex;align-items:center;gap:3px}
.ms-tag .x{cursor:pointer;font-weight:700;color:#94a3b8}
.ms-tag .x:hover{color:#ef4444}

.fg{margin-bottom:8px}
.fg label{display:block;font-size:11px;font-weight:500;color:#94a3b8;margin-bottom:3px}
.fg select,.fg input{width:100%;padding:6px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:12px;outline:none;transition:border .2s}
.fg select:focus,.fg input:focus{border-color:#3b82f6}

.btn-row{display:flex;gap:8px;margin:8px 0 4px}
.btn{flex:1;padding:8px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:all .15s}
.btn-primary{background:#3b82f6;color:#fff}
.btn-primary:hover{background:#2563eb}
.btn-area{background:#8b5cf6;color:#fff}
.btn-area:hover{background:#7c3aed}
.btn-area.active{background:#ef4444}
.btn-ghost{background:#1e293b;color:#94a3b8;border:1px solid #334155}
.btn-ghost:hover{background:#334155;color:#e2e8f0}

.toggle-row{display:flex;align-items:center;gap:8px;padding:5px 0;font-size:12px;color:#94a3b8}
.toggle-row input[type=checkbox]{accent-color:#3b82f6}

.date-presets{display:flex;gap:4px;margin-bottom:10px;flex-wrap:wrap}
.dp-btn{padding:4px 8px;background:#1e293b;border:1px solid #334155;border-radius:4px;color:#94a3b8;font-size:10px;cursor:pointer;transition:all .15s}
.dp-btn:hover{background:#334155;color:#e2e8f0;border-color:#3b82f6}
input[type=date]{color-scheme:dark}

/* Legend */
.legend-item{display:flex;align-items:center;gap:8px;margin-bottom:3px;font-size:11px;color:#cbd5e1}
.legend-swatch{width:18px;height:4px;border-radius:2px}
.legend-dot{width:8px;height:8px;border-radius:50%}

/* ── Overlap picker popup ── */
.overlap-popup{max-height:200px;overflow-y:auto;min-width:200px}
.overlap-item{padding:6px 8px;cursor:pointer;border-bottom:1px solid #eee;font-size:12px;transition:background .15s}
.overlap-item:hover{background:#e0f2fe}
.overlap-item:last-child{border-bottom:none}
.overlap-item b{color:#1e40af}

/* Detail panel */
.detail-panel{border-top:1px solid #334155;padding:14px 16px;animation:slideUp .25s ease}
@keyframes slideUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.detail-prompt{font-size:12px;line-height:1.6;color:#cbd5e1;margin-bottom:10px;padding:10px;background:#1e293b;border-radius:6px;border-left:3px solid #3b82f6}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px}
.di .dl{font-size:10px;color:#64748b;font-weight:500}
.di .dv{font-size:12px;color:#e2e8f0;font-weight:600}
.detail-img{width:100%;border-radius:6px;margin-bottom:10px;cursor:pointer;border:1px solid #334155;transition:border .2s}
.detail-img:hover{border-color:#3b82f6}
.detail-actions a{display:flex;align-items:center;gap:6px;padding:8px 12px;margin-bottom:5px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#cbd5e1;text-decoration:none;font-size:11px;font-weight:500;transition:all .15s}
.detail-actions a:hover{background:#334155;color:#fff;border-color:#3b82f6}
.episode-nav{margin:10px 0;padding:10px;background:#111827;border:1px solid #334155;border-radius:8px}
.episode-nav-top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}
.episode-nav-label{font-size:11px;color:#cbd5e1;font-weight:600}
.episode-nav-top button{padding:4px 8px;border:1px solid #334155;background:#1e293b;color:#cbd5e1;border-radius:5px;font-size:10px;cursor:pointer}
.episode-nav-top button:hover{background:#334155}
.episode-nav-ctrl{display:grid;grid-template-columns:auto 1fr auto;gap:6px}
.episode-nav-ctrl button,.episode-nav-ctrl select{padding:6px 8px;border:1px solid #334155;background:#1e293b;color:#e2e8f0;border-radius:6px;font-size:11px}
.episode-nav-ctrl button{cursor:pointer}
.episode-nav-ctrl button:disabled{opacity:.45;cursor:not-allowed}
.focus-bar{margin:0 16px 8px;padding:8px 10px;background:#065f46;border:1px solid #047857;border-radius:8px;display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:11px;color:#d1fae5}
.focus-bar button{padding:4px 8px;border:none;border-radius:5px;background:#ecfdf5;color:#065f46;font-size:10px;font-weight:700;cursor:pointer}

/* Map */
.map-wrap{flex:1;position:relative}
#map{width:100%;height:100%}
.loader{position:absolute;inset:0;background:rgba(15,23,42,.6);display:flex;align-items:center;justify-content:center;z-index:1000}
.spinner{width:36px;height:36px;border:3px solid #334155;border-top-color:#3b82f6;border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.hidden{display:none!important}

/* Area selection banner */
.area-banner{position:absolute;top:12px;left:50%;transform:translateX(-50%);background:#7c3aed;color:#fff;padding:8px 20px;border-radius:8px;font-size:13px;font-weight:600;z-index:800;box-shadow:0 4px 16px rgba(0,0,0,.3);display:flex;align-items:center;gap:10px}
.area-banner button{background:#fff;color:#7c3aed;border:none;border-radius:4px;padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer}
.area-active-banner{position:absolute;top:12px;left:50%;transform:translateX(-50%);background:#059669;color:#fff;padding:8px 20px;border-radius:8px;font-size:13px;font-weight:600;z-index:800;box-shadow:0 4px 16px rgba(0,0,0,.3);display:flex;align-items:center;gap:10px;animation:fadeIn .3s ease}
.area-active-banner button{background:#fff;color:#059669;border:none;border-radius:4px;padding:4px 10px;font-size:11px;font-weight:700;cursor:pointer}
@keyframes fadeIn{from{opacity:0;transform:translateX(-50%) translateY(-8px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}

/* ── Color-by floating control on the map ── */
.color-control{position:absolute;bottom:30px;left:12px;z-index:800;background:rgba(15,23,42,.92);backdrop-filter:blur(8px);border:1px solid #334155;border-radius:10px;padding:8px 6px;box-shadow:0 4px 20px rgba(0,0,0,.4);display:flex;flex-direction:column;gap:2px;min-width:52px;align-items:center}
.color-control .cc-title{font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#64748b;margin-bottom:2px;text-align:center}
.cc-btn{width:44px;padding:5px 0;border:2px solid transparent;border-radius:6px;background:#1e293b;color:#cbd5e1;font-size:9px;font-weight:600;cursor:pointer;text-align:center;transition:all .15s;line-height:1.2}
.cc-btn:hover{background:#334155;color:#fff}
.cc-btn.active{border-color:#3b82f6;background:#1e3a5f;color:#60a5fa}
.cc-btn .cc-icon{font-size:14px;display:block;margin-bottom:1px}
.cc-btn.random-btn.active{border-color:#f59e0b;background:#451a03;color:#fbbf24}

/* Scrollbar */
.sidebar::-webkit-scrollbar{width:5px}
.sidebar::-webkit-scrollbar-track{background:#0f172a}
.sidebar::-webkit-scrollbar-thumb{background:#334155;border-radius:3px}
.ms-dd::-webkit-scrollbar{width:4px}
.ms-dd::-webkit-scrollbar-thumb{background:#475569;border-radius:3px}

@media(max-width:800px){
  body{flex-direction:column}
  .sidebar{width:100%;height:45%;min-height:250px}
  .map-wrap{height:55%}
}
</style>
</head>
<body>

<!-- ═══════ SIDEBAR ═══════ -->
<div class="sidebar">
  <div class="sidebar-header">
    <h1>🗺️ FOMO Episodes Explorer</h1>
    <p>curated_fomo_episodes · interactive visualization</p>
  </div>

  <div class="stats-row">
    <div class="stat-card"><div class="stat-num" id="ep-count">–</div><div class="stat-label">Loaded</div></div>
    <div class="stat-card"><div class="stat-num" id="total-count">–</div><div class="stat-label">Total</div></div>
    <div class="stat-card"><div class="stat-num" id="visible-count">–</div><div class="stat-label">Visible</div></div>
  </div>
  <div class="focus-bar hidden" id="focus-bar">
    <span id="focus-bar-text"></span>
    <button onclick="clearEpisodeFocus()">Show all</button>
  </div>

  <!-- Jump to Dataset Episode -->
  <div class="section">
    <div class="section-title">Jump to Dataset Episode</div>
    <div style="display:flex;gap:8px;align-items:flex-end">
      <div class="fg" style="flex:1;margin-bottom:0">
        <label>Dataset Index</label>
        <input type="number" id="f-dataset-index" placeholder="e.g. 42" min="0"
               onkeydown="if(event.key==='Enter') lookupAndGo()">
      </div>
      <button class="btn btn-primary" style="flex-shrink:0;margin-bottom:0" onclick="lookupAndGo()">Go</button>
    </div>
    <div id="lookup-result" style="font-size:11px;margin-top:5px"></div>
    <div class="btn-row" style="margin-top:6px">
      <button class="btn btn-ghost" onclick="syncFromVisualizer()">↻ Sync from Visualizer</button>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="toggle-live-sync" onchange="toggleLiveSync()">
      <label for="toggle-live-sync">Live sync with visualizer</label>
    </div>
  </div>

  <!-- Filters -->
  <div class="section">
    <div class="section-title">Filters <span style="font-weight:400;color:#64748b">(multi-select)</span></div>

    <div id="ms-project" class="ms-wrap" data-key="project"><label>Project</label></div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div id="ms-way_type" class="ms-wrap" data-key="way_type"><label>Way Type</label></div>
      <div id="ms-surface" class="ms-wrap" data-key="surface"><label>Surface</label></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div id="ms-weather" class="ms-wrap" data-key="weather"><label>Weather</label></div>
      <div id="ms-time_of_the_day" class="ms-wrap" data-key="time_of_the_day"><label>Time of Day</label></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div id="ms-fleet_name" class="ms-wrap" data-key="fleet_name"><label>Fleet</label></div>
      <div id="ms-robot_id" class="ms-wrap" data-key="robot_id"><label>Robot</label></div>
    </div>

    <div class="section-title" style="margin-top:8px">Date Range</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div class="fg"><label>From</label>
        <input type="date" id="f-date-from" value="">
      </div>
      <div class="fg"><label>To</label>
        <input type="date" id="f-date-to" value="">
      </div>
    </div>
    <div class="date-presets">
      <button class="dp-btn" onclick="setDatePreset('today')">Today</button>
      <button class="dp-btn" onclick="setDatePreset('7d')">7 days</button>
      <button class="dp-btn" onclick="setDatePreset('30d')">30 days</button>
      <button class="dp-btn" onclick="setDatePreset('90d')">90 days</button>
      <button class="dp-btn" onclick="setDatePreset('all')">All time</button>
    </div>

    <div class="fg">
      <label>Search prompt</label>
      <input type="text" id="f-prompt" placeholder="Type to filter by prompt text…">
    </div>
    <div class="fg">
      <label>Rosbag name <span style="font-weight:400;color:#64748b">(click for BQ list)</span></label>
      <input type="text" id="f-rosbag" list="dl-rosbag" placeholder="Pick from list or type substring…" autocomplete="off">
      <datalist id="dl-rosbag"></datalist>
    </div>
    <div class="fg hidden" id="ep-idx-section">
      <label>Episode Indices <span style="font-weight:400;color:#64748b">(select to filter)</span></label>
      <div id="ep-idx-ms" class="ms-wrap" style="margin-bottom:0"></div>
    </div>
    <div class="fg"><label>Limit</label>
      <select id="f-limit">
        <option value="200">200</option>
        <option value="500">500</option>
        <option value="1000">1000</option>
        <option value="1500" selected>All (~1500)</option>
        <option value="3000">3000</option>
        <option value="5000">5000</option>
      </select>
    </div>
    <!-- Hidden select kept for JS compatibility -->
    <select id="color-by" class="hidden">
      <option value="way_type" selected>Way Type</option>
      <option value="surface">Surface</option>
      <option value="weather">Weather</option>
      <option value="time_of_the_day">Time of Day</option>
      <option value="robot_id">Robot</option>
      <option value="split">Split</option>
      <option value="random">Random</option>
    </select>

    <div class="btn-row">
      <button class="btn btn-primary" onclick="loadEpisodes(activeBbox)">🔍 Apply</button>
      <button class="btn btn-ghost" onclick="resetFilters()">↺ Reset</button>
    </div>
    <div class="btn-row">
      <button class="btn btn-area" id="btn-area" onclick="toggleAreaSelect()">📐 Select Area</button>
    </div>

    <div class="toggle-row">
      <input type="checkbox" id="toggle-usbs">
      <label for="toggle-usbs">Show USB bot locations (fomo_usbs)</label>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="toggle-start-end" checked>
      <label for="toggle-start-end">Show start/end markers</label>
    </div>
  </div>

  <!-- Legend -->
  <div class="section" id="legend-section">
    <div class="section-title">Legend</div>
    <div id="legend"></div>
  </div>

  <!-- Detail -->
  <div class="detail-panel hidden" id="detail-panel">
    <div class="section-title">Episode Details</div>
    <div class="episode-nav hidden" id="episode-nav">
      <div class="episode-nav-top">
        <div class="episode-nav-label" id="episode-nav-label"></div>
        <button id="focus-toggle-btn" onclick="toggleFocusForSelected()">Focus this rosbag</button>
      </div>
      <div class="episode-nav-ctrl">
        <button id="episode-prev-btn" onclick="goEpisodeInGroup(-1)">← Prev</button>
        <select id="episode-nav-select" onchange="jumpToEpisodeInGroup(this.value)"></select>
        <button id="episode-next-btn" onclick="goEpisodeInGroup(1)">Next →</button>
      </div>
    </div>
    <div id="detail-content"></div>
  </div>
</div>

<!-- ═══════ MAP ═══════ -->
<div class="map-wrap">
  <div id="map"></div>
  <div class="loader hidden" id="loader"><div class="spinner"></div></div>
  <div class="area-banner hidden" id="area-banner">
    📐 Draw a rectangle on the map
    <button onclick="cancelAreaSelect()">✕ Cancel</button>
  </div>
  <div class="area-active-banner hidden" id="area-active-banner">
    📐 Showing results in selected area
    <button onclick="clearAreaAndReload()">✕ Clear area</button>
  </div>
  <!-- ── Floating Color-by Control ── -->
  <div class="color-control" id="color-control">
    <div class="cc-title">Color</div>
    <button class="cc-btn active" data-color="way_type" onclick="setColorBy('way_type',this)" title="Color by Way Type"><span class="cc-icon">🛤️</span>Way</button>
    <button class="cc-btn" data-color="surface" onclick="setColorBy('surface',this)" title="Color by Surface"><span class="cc-icon">🧱</span>Surf</button>
    <button class="cc-btn" data-color="weather" onclick="setColorBy('weather',this)" title="Color by Weather"><span class="cc-icon">🌤️</span>Wea</button>
    <button class="cc-btn" data-color="time_of_the_day" onclick="setColorBy('time_of_the_day',this)" title="Color by Time of Day"><span class="cc-icon">🕐</span>Time</button>
    <button class="cc-btn" data-color="robot_id" onclick="setColorBy('robot_id',this)" title="Color by Robot"><span class="cc-icon">🤖</span>Bot</button>
    <button class="cc-btn" data-color="split" onclick="setColorBy('split',this)" title="One color per split (rosbag + episode index)"><span class="cc-icon">&#x2702;&#xFE0F;</span>Split</button>
    <button class="cc-btn random-btn" data-color="random" onclick="setColorBy('random',this)" title="Random colors – best for overlapping routes"><span class="cc-icon">🎲</span>Rand</button>
  </div>
</div>

<!-- ═══════ JS ═══════ -->
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
<script>
// ══════════════════════════════════════════════════════════════════════════
// COLOUR PALETTES
// ══════════════════════════════════════════════════════════════════════════
const CMAPS = {
  way_type:        {Sidewalk:'#3b82f6',Crosswalk:'#10b981',Driveway:'#f59e0b',Crossroad:'#ef4444',Parking_lot:'#8b5cf6'},
  surface:         {Asphalt:'#6b7280',Zebra_crossing:'#f59e0b',Pavers:'#d97706',Dirt:'#92400e'},
  weather:         {Sunny:'#fbbf24',Rainy:'#3b82f6',Sun_glare:'#f97316',Snowy:'#94a3b8'},
  time_of_the_day: {Daytime:'#fbbf24',Night:'#6366f1',Dusk:'#f97316'},
  robot_id:        {},  // dynamically generated
  split:           {}   // built per render from rosbag_name + episode_index
};
const SPLIT_KEY_SEP = '\x01';

/** Stable id for one curated row / split (for coloring). */
function splitColorKey(ep, idx){
  const bag = ep.rosbag_name != null ? String(ep.rosbag_name) : '';
  const eiNum = Number(ep.episode_index);
  if(bag !== '' && Number.isFinite(eiNum)) return bag + SPLIT_KEY_SEP + eiNum;
  if(ep.uuid) return 'uuid' + SPLIT_KEY_SEP + String(ep.uuid);
  return 'row' + SPLIT_KEY_SEP + idx;
}

function splitKeyLabel(key){
  if(key.startsWith('uuid'+SPLIT_KEY_SEP)){
    const u = key.slice(5);
    return 'uuid ' + (u.length > 12 ? u.slice(0,8)+'…' : u);
  }
  if(key.startsWith('row'+SPLIT_KEY_SEP)) return 'row ' + key.slice(4);
  const i = key.indexOf(SPLIT_KEY_SEP);
  if(i < 0) return key;
  const bagPath = key.slice(0, i);
  const epn = key.slice(i + 1);
  const bagShort = bagPath.includes('/') ? bagPath.split('/').pop() : bagPath;
  return bagShort + ' · ep ' + epn;
}

const ROBOT_COLORS = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899','#14b8a6','#f97316','#6366f1','#84cc16','#06b6d4','#e11d48','#a855f7','#22c55e','#eab308','#64748b'];
const RANDOM_POOL = [
  '#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#10b981',
  '#14b8a6','#06b6d4','#0ea5e9','#3b82f6','#6366f1','#8b5cf6','#a855f7',
  '#d946ef','#ec4899','#f43f5e','#fb923c','#a3e635','#2dd4bf','#38bdf8',
  '#818cf8','#c084fc','#f472b6','#fbbf24','#34d399','#67e8f9','#c4b5fd'
];
const DFLT = '#64748b';

function buildSplitColorMap(){
  const keys = new Set();
  episodes.forEach((ep, idx) => {
    if(!isEpisodeVisible(ep)) return;
    keys.add(splitColorKey(ep, idx));
  });
  const sorted = [...keys].sort();
  const map = {};
  sorted.forEach((k, j) => { map[k] = RANDOM_POOL[j % RANDOM_POOL.length]; });
  return { map, sorted };
}

/**
 * Default color mode is Way Type; Sidewalk is blue (#3b82f6) so many splits look identical.
 * Switch to Split coloring when multiple splits are loaded and Way would not separate them.
 */
function maybeAutoColorBySplit(){
  if(episodes.length < 2) return;
  const sel = document.getElementById('color-by');
  if(sel.value !== 'way_type') return;
  const splitKeys = new Set();
  episodes.forEach((ep, idx) => splitKeys.add(splitColorKey(ep, idx)));
  if(splitKeys.size < 2) return;
  const rosbagQ = document.getElementById('f-rosbag').value.trim();
  const wayVals = new Set(episodes.map(e => e.way_type ?? ''));
  const sameWay = wayVals.size <= 1;
  if(!rosbagQ && !sameWay) return;
  sel.value = 'split';
  document.querySelectorAll('.cc-btn').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector('.cc-btn[data-color="split"]');
  if(btn) btn.classList.add('active');
}

// ══════════════════════════════════════════════════════════════════════════
// STATE
// ══════════════════════════════════════════════════════════════════════════
let map, epLayer, usbLayer, drawControl, drawnItems;
let episodes = [];
let selPolyline = null;
let selectedEpisodeIdx = null;
let focusedGroupKey = null;
let focusedGroupLabel = null;
let episodeIndexFilter = new Set();
let episodeMainLayerByIdx = {};
let usbsLoaded = false;
let areaMode = false;
let filterOptions = {};

// Multi-select state: key -> Set of selected values
const msState = {};

// ══════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  // --- Map layers ---
  const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
    attribution:'© OpenStreetMap',maxZoom:19
  });
  const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
    attribution:'© Esri',maxZoom:19
  });
  const dark = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    attribution:'© CARTO',maxZoom:19
  });
  const light = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',{
    attribution:'© CARTO',maxZoom:19
  });
  const topo = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{
    attribution:'© OpenTopoMap',maxZoom:17
  });

  map = L.map('map',{zoomControl:true,layers:[osm]}).setView([33.97,-98.22],5);

  L.control.layers({
    'OpenStreetMap': osm,
    '🛰️ Satellite': satellite,
    '🌑 Dark': dark,
    '⬜ Light / Minimal': light,
    '🏔️ Topo': topo
  }, {}, {position:'topright'}).addTo(map);

  epLayer  = L.layerGroup().addTo(map);
  usbLayer = L.layerGroup();

  // --- Draw control for area selection ---
  drawnItems = new L.FeatureGroup();
  map.addLayer(drawnItems);

  map.on(L.Draw.Event.CREATED, onAreaDrawn);

  document.getElementById('color-by').addEventListener('change', () => { renderMap(); renderLegend(); });
  document.getElementById('toggle-usbs').addEventListener('change', toggleUsbs);
  document.getElementById('toggle-start-end').addEventListener('change', () => { renderMap(); renderLegend(); });

  // Close dropdowns when clicking outside
  document.addEventListener('click', (e) => {
    if(!e.target.closest('.ms-wrap')){
      document.querySelectorAll('.ms-dd.show').forEach(d=>d.classList.remove('show'));
      document.querySelectorAll('.ms-btn.open').forEach(b=>b.classList.remove('open'));
    }
  });

  loadFilters().then(() => loadEpisodes());

  const rosbagEl = document.getElementById('f-rosbag');
  rosbagEl.addEventListener('focus', ensureRosbagDatalist);
  rosbagEl.addEventListener('mousedown', ensureRosbagDatalist);
});


// ══════════════════════════════════════════════════════════════════════════
// MULTI-SELECT COMPONENT
// ══════════════════════════════════════════════════════════════════════════
function buildMultiSelect(wrapId, key, options){
  const wrap = document.getElementById(wrapId);
  msState[key] = new Set();

  // Button
  const btn = document.createElement('div');
  btn.className = 'ms-btn';
  btn.innerHTML = '<span class="ms-text">All</span><span class="arrow">▼</span>';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const dd = wrap.querySelector('.ms-dd');
    const wasOpen = dd.classList.contains('show');
    // Close all others
    document.querySelectorAll('.ms-dd.show').forEach(d=>d.classList.remove('show'));
    document.querySelectorAll('.ms-btn.open').forEach(b=>b.classList.remove('open'));
    if(!wasOpen){ dd.classList.add('show'); btn.classList.add('open'); }
  });
  wrap.appendChild(btn);

  // Dropdown
  const dd = document.createElement('div');
  dd.className = 'ms-dd';

  // Actions row
  const acts = document.createElement('div');
  acts.className = 'ms-actions';
  acts.innerHTML = '<button onclick="msSelectAll(\''+key+'\')">All</button><button onclick="msSelectNone(\''+key+'\')">None</button>';
  dd.appendChild(acts);

  options.forEach(opt => {
    const lbl = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = opt;
    cb.dataset.mskey = key;
    cb.addEventListener('change', () => {
      if(cb.checked) msState[key].add(opt);
      else msState[key].delete(opt);
      updateMsDisplay(key);
    });
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(opt));
    dd.appendChild(lbl);
  });
  wrap.appendChild(dd);

  // Tags row
  const tagRow = document.createElement('div');
  tagRow.className = 'ms-tag-row';
  tagRow.id = 'tags-'+key;
  wrap.appendChild(tagRow);
}

function updateMsDisplay(key){
  const wrap = document.getElementById('ms-'+key);
  const btn = wrap.querySelector('.ms-text');
  const tagRow = document.getElementById('tags-'+key);
  const sel = msState[key];

  if(sel.size === 0){
    btn.textContent = 'All';
    tagRow.innerHTML = '';
  } else {
    btn.textContent = sel.size + ' selected';
    tagRow.innerHTML = '';
    sel.forEach(v => {
      const tag = document.createElement('span');
      tag.className = 'ms-tag';
      tag.innerHTML = esc(v) + ' <span class="x" onclick="msRemove(\''+key+'\',\''+esc(v)+'\')">×</span>';
      tagRow.appendChild(tag);
    });
  }
}

function msSelectAll(key){
  const wrap = document.getElementById('ms-'+key);
  wrap.querySelectorAll('.ms-dd input[type=checkbox]').forEach(cb => { cb.checked = true; msState[key].add(cb.value); });
  updateMsDisplay(key);
}
function msSelectNone(key){
  const wrap = document.getElementById('ms-'+key);
  wrap.querySelectorAll('.ms-dd input[type=checkbox]').forEach(cb => { cb.checked = false; });
  msState[key].clear();
  updateMsDisplay(key);
}
function msRemove(key, val){
  msState[key].delete(val);
  const wrap = document.getElementById('ms-'+key);
  wrap.querySelectorAll('.ms-dd input[type=checkbox]').forEach(cb => {
    if(cb.value===val) cb.checked=false;
  });
  updateMsDisplay(key);
}
function getMsValue(key){
  const sel = msState[key];
  if(!sel || sel.size===0) return 'all';
  return [...sel].join(',');
}


// ══════════════════════════════════════════════════════════════════════════
// JUMP TO DATASET EPISODE
// ══════════════════════════════════════════════════════════════════════════
async function lookupAndGo() {
  const val = document.getElementById('f-dataset-index').value.trim();
  if (!val) return;
  const n = parseInt(val, 10);
  if (isNaN(n)) return;

  const resultEl = document.getElementById('lookup-result');
  resultEl.style.color = '#64748b';
  resultEl.textContent = 'Looking up…';

  let lookup;
  try {
    showLoader(true);
    const r = await fetch('/api/lookup_episode?dataset_index=' + n);
    showLoader(false);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      resultEl.style.color = '#ef4444';
      resultEl.textContent = '✗ ' + (d.error || 'Not found');
      return;
    }
    lookup = await r.json();
  } catch(e) {
    showLoader(false);
    resultEl.style.color = '#ef4444';
    resultEl.textContent = '✗ Lookup failed';
    return;
  }

  resultEl.style.color = '#64748b';
  resultEl.textContent = '→ ' + lookup.rosbag_name + ' · ep ' + lookup.episode_index;

  document.getElementById('f-rosbag').value = lookup.rosbag_name;
  await loadEpisodes(activeBbox);  // builds the episode index dropdown (all visible)

  // Apply episode index filter to show only the target split
  const epIdxStr = String(lookup.episode_index);
  episodeIndexFilter.clear();
  episodeIndexFilter.add(epIdxStr);
  document.querySelectorAll('.ep-idx-cb').forEach(cb => { cb.checked = (cb.value === epIdxStr); });
  updateEpIdxDisplay();
  renderMap();  // re-render with filter applied

  const targetEpIdx = episodes.findIndex(ep => Number(ep.episode_index) === lookup.episode_index);
  if (targetEpIdx >= 0) {
    const pl = episodeMainLayerByIdx[targetEpIdx];
    if (pl) selectEp(targetEpIdx, pl);
  }
}


const BRIDGE_URL = 'http://127.0.0.1:18765';
let _liveSyncInterval = null;
let _lastLiveSyncEpisodeId = null;

async function syncFromVisualizer() {
  const resultEl = document.getElementById('lookup-result');
  resultEl.style.color = '#64748b';
  resultEl.textContent = 'Requesting episode from visualizer…';

  // Ask the visualizer to re-POST its current episode (same as curator's "Sync from visualizer")
  try {
    await fetch(BRIDGE_URL + '/request-resync', {cache: 'no-store', mode: 'cors'});
  } catch(e) {
    resultEl.style.color = '#ef4444';
    resultEl.textContent = '✗ Curator bridge not running (port 18765)';
    return;
  }

  // Wait for the visualizer to POST back, then read the episode
  await new Promise(r => setTimeout(r, 600));

  let episodeId;
  try {
    const r = await fetch(BRIDGE_URL + '/current_episode', {cache: 'no-store', mode: 'cors'});
    if (!r.ok) { resultEl.style.color = '#ef4444'; resultEl.textContent = '✗ Bridge error'; return; }
    const d = await r.json();
    if (d.episodeId === null || d.episodeId === undefined) {
      resultEl.style.color = '#ef4444';
      resultEl.textContent = '✗ No episode received yet';
      return;
    }
    episodeId = d.episodeId;
  } catch(e) {
    resultEl.style.color = '#ef4444';
    resultEl.textContent = '✗ Could not reach bridge';
    return;
  }

  document.getElementById('f-dataset-index').value = episodeId;
  await lookupAndGo();
}

function toggleLiveSync() {
  const on = document.getElementById('toggle-live-sync').checked;
  if (on) {
    _lastLiveSyncEpisodeId = null;
    _liveSyncInterval = setInterval(async () => {
      try {
        const r = await fetch(BRIDGE_URL + '/current_episode', {cache: 'no-store', mode: 'cors'});
        if (!r.ok) return;
        const d = await r.json();
        const id = d.episodeId;
        if (id !== null && id !== undefined && id !== _lastLiveSyncEpisodeId) {
          _lastLiveSyncEpisodeId = id;
          document.getElementById('f-dataset-index').value = id;
          await lookupAndGo();
        }
      } catch(e) { /* bridge not running */ }
    }, 2000);
  } else {
    if (_liveSyncInterval) { clearInterval(_liveSyncInterval); _liveSyncInterval = null; }
  }
}


// ══════════════════════════════════════════════════════════════════════════
// LOAD FILTERS
// ══════════════════════════════════════════════════════════════════════════
let rosbagNamesLoaded = false;
let rosbagNamesLoading = false;
async function ensureRosbagDatalist(){
  if(rosbagNamesLoaded || rosbagNamesLoading) return;
  rosbagNamesLoading = true;
  try{
    const r = await fetch('/api/rosbag_names');
    const d = await r.json();
    const dl = document.getElementById('dl-rosbag');
    dl.innerHTML = '';
    (d.names || []).forEach(name => {
      const o = document.createElement('option');
      o.value = name;
      dl.appendChild(o);
    });
    rosbagNamesLoaded = true;
  }catch(e){ console.error('ensureRosbagDatalist', e); }
  finally{ rosbagNamesLoading = false; }
}

async function loadFilters(){
  try{
    const r = await fetch('/api/filters');
    const d = await r.json();
    filterOptions = d;
    document.getElementById('total-count').textContent = d.total_count;

    // Build robot color map
    if(d.robot_id){
      d.robot_id.forEach((r,i) => { CMAPS.robot_id[r] = ROBOT_COLORS[i % ROBOT_COLORS.length]; });
    }

    for(const k of ['robot_id','fleet_name','way_type','surface','weather','time_of_the_day','project']){
      if(d[k]) buildMultiSelect('ms-'+k, k, d[k]);
    }
  }catch(e){ console.error('loadFilters',e); }
}


// ══════════════════════════════════════════════════════════════════════════
// LOAD EPISODES
// ══════════════════════════════════════════════════════════════════════════
async function loadEpisodes(bbox){
  showLoader(true);
  try{
    const p = new URLSearchParams();
    for(const k of ['robot_id','fleet_name','way_type','surface','weather','time_of_the_day','project']){
      const v = getMsValue(k);
      if(v!=='all') p.set(k,v);
    }
    const dateFrom=document.getElementById('f-date-from').value;
    const dateTo=document.getElementById('f-date-to').value;
    if(dateFrom) p.set('date_from',dateFrom);
    if(dateTo) p.set('date_to',dateTo);
    p.set('limit', document.getElementById('f-limit').value);
    if(bbox) p.set('bbox', bbox);
    const rosbag = document.getElementById('f-rosbag').value.trim();
    if(rosbag) p.set('rosbag_name', rosbag);

    const r = await fetch('/api/episodes?'+p);
    episodes = await r.json();

    // Client-side prompt search
    const q = document.getElementById('f-prompt').value.trim().toLowerCase();
    if(q) episodes = episodes.filter(e => e.prompt && e.prompt.toLowerCase().includes(q));

    document.getElementById('ep-count').textContent = episodes.length;
    if(focusedGroupKey && !getSortedGroupIndices(focusedGroupKey).length){
      focusedGroupKey = null;
      focusedGroupLabel = null;
    }
    maybeAutoColorBySplit();
    refreshEpisodeIndexFilter();
    renderMap();
    renderLegend();
    if(!bbox) fitBounds();
  }catch(e){ console.error('loadEpisodes',e); alert('Error loading episodes'); }
  finally{ showLoader(false); }
}


// ══════════════════════════════════════════════════════════════════════════
// COLOR-BY CONTROL (floating on map)
// ══════════════════════════════════════════════════════════════════════════
function setColorBy(value, btnEl){
  document.getElementById('color-by').value = value;
  // Update active state on floating buttons
  document.querySelectorAll('.cc-btn').forEach(b => b.classList.remove('active'));
  if(btnEl) btnEl.classList.add('active');
  renderMap();
  renderLegend();
}


// ══════════════════════════════════════════════════════════════════════════
// RENDER MAP
// ══════════════════════════════════════════════════════════════════════════
function renderMap(){
  const prevSelectedIdx = selectedEpisodeIdx;
  epLayer.clearLayers();
  episodeMainLayerByIdx = {};
  selPolyline = null;

  const colorBy = document.getElementById('color-by').value;
  const isRandom = (colorBy === 'random');
  const isSplit = (colorBy === 'split');
  const cm = (isRandom || isSplit) ? {} : (CMAPS[colorBy]||{});
  const showSE = document.getElementById('toggle-start-end').checked;

  let splitColorMap = null;
  if(isSplit) splitColorMap = buildSplitColorMap().map;

  // Shuffle pool for random mode so nearby episodes get different colors
  let randomColors = [];
  if(isRandom){
    randomColors = [...RANDOM_POOL];
    for(let j=randomColors.length-1;j>0;j--){ const k=Math.floor(Math.random()*(j+1)); [randomColors[j],randomColors[k]]=[randomColors[k],randomColors[j]]; }
  }

  // Build spatial index for overlap detection
  const cellMap = {};  // grid cell -> [indices]

  episodes.forEach((ep,i) => {
    if(!isEpisodeVisible(ep)) return;

    const color = isRandom ? randomColors[i % randomColors.length]
      : isSplit ? (splitColorMap[splitColorKey(ep,i)] || DFLT)
      : (cm[ep[colorBy]] || DFLT);
    let path = (ep.gps_path && ep.gps_path.length) ? ep.gps_path : null;
    if(!path){
      if(ep.initial_gps_point && ep.final_gps_point) path=[ep.initial_gps_point,ep.final_gps_point];
      else if(ep.initial_gps_point) path=[ep.initial_gps_point];
    }
    if(!path || !path.length) return;

    // Register in spatial grid for overlap detection
    const mid = path[Math.floor(path.length/2)];
    const cellKey = (mid[0]*100|0)+','+(mid[1]*100|0);
    if(!cellMap[cellKey]) cellMap[cellKey]=[];
    cellMap[cellKey].push(i);

    if(path.length===1){
      const m = L.circleMarker(path[0],{radius:6,fillColor:color,color:'#fff',weight:1,fillOpacity:.85});
      m._epIdx = i;
      episodeMainLayerByIdx[i] = m;
      m.on('click',(e)=>handleClick(e,i,m));
      m.bindTooltip(tip(ep),{direction:'top'});
      epLayer.addLayer(m);
    } else {
      const pl = L.polyline(path,{color,weight:4,opacity:.75,lineCap:'round'});
      pl._epIdx = i;
      episodeMainLayerByIdx[i] = pl;

      const layers = [pl];
      if(showSE){
        layers.push(L.circleMarker(path[0],{radius:4,fillColor:'#10b981',color:'#fff',weight:1.5,fillOpacity:1}));
        layers.push(L.circleMarker(path[path.length-1],{radius:4,fillColor:'#ef4444',color:'#fff',weight:1.5,fillOpacity:1}));
      }

      pl.on('mouseover',function(){ if(selPolyline!==this) this.setStyle({weight:7,opacity:1}); });
      pl.on('mouseout', function(){ if(selPolyline!==this) this.setStyle({weight:4,opacity:.75}); });

      const grp = L.featureGroup(layers);
      grp._epIdx = i;
      grp.on('click',(e)=>handleClick(e,i,pl));
      pl.bindTooltip(tip(ep),{sticky:true});
      epLayer.addLayer(grp);
    }
  });

  // Store cellMap for overlap detection
  window._cellMap = cellMap;

  const visibleCount = Object.keys(episodeMainLayerByIdx).length;
  document.getElementById('visible-count').textContent = visibleCount;
  updateFocusBar();

  if(prevSelectedIdx !== null && episodeMainLayerByIdx[prevSelectedIdx]){
    selectEp(prevSelectedIdx, episodeMainLayerByIdx[prevSelectedIdx], {focusMap:false});
  } else {
    selectedEpisodeIdx = null;
    document.getElementById('detail-panel').classList.add('hidden');
    updateEpisodeNavigator();
  }
}

function tip(e){
  const dur = e.duration ? e.duration.toFixed(1)+'s' : '–';
  const pts = (e.gps_path||[]).length;
  const bag = e.rosbag_name ? e.rosbag_name.split('/').pop() : '–';
  return '<b>🤖 '+esc(e.robot_id||'?')+'</b> · ep '+(e.episode_index??'?')
    +'<br>'+esc(e.way_type||'–')+' · '+esc(e.surface||'–')+' · '+esc(e.weather||'–')
    +'<br>⏱ '+dur+' · 📍 '+pts+' pts'
    +'<br>📦 <em>'+esc(bag)+'</em>'
    +'<br>📅 '+(e.timestamp?e.timestamp.substring(0,16).replace('T',' '):'–');
}


// ══════════════════════════════════════════════════════════════════════════
// OVERLAP HANDLING – click near same area shows picker popup
// ══════════════════════════════════════════════════════════════════════════
function handleClick(e, idx, layer){
  // Find nearby episodes at this click location
  const latlng = e.latlng;
  const nearby = findNearbyEpisodes(latlng, 15);  // 15px tolerance

  if(nearby.length <= 1){
    selectEp(idx, layer);
  } else {
    // Show overlap picker popup
    showOverlapPicker(latlng, nearby);
  }
}

function findNearbyEpisodes(latlng, pxTolerance){
  const point = map.latLngToContainerPoint(latlng);
  const found = [];
  const seen = new Set();

  epLayer.eachLayer(layer => {
    const idx = layer._epIdx;
    if(idx===undefined || seen.has(idx)) return;

    // Check polylines and markers within the group
    const checkLayer = (l) => {
      if(seen.has(idx)) return;
      if(l.getLatLngs){
        // polyline
        const pts = l.getLatLngs();
        for(const p of pts){
          const cp = map.latLngToContainerPoint(p);
          if(cp.distanceTo(point) < pxTolerance){ seen.add(idx); found.push(idx); return; }
        }
      } else if(l.getLatLng){
        const cp = map.latLngToContainerPoint(l.getLatLng());
        if(cp.distanceTo(point) < pxTolerance){ seen.add(idx); found.push(idx); }
      }
    };

    if(layer.eachLayer) layer.eachLayer(checkLayer);
    else checkLayer(layer);
  });

  return found;
}

function showOverlapPicker(latlng, indices){
  const colorBy = document.getElementById('color-by').value;
  const cm = CMAPS[colorBy]||{};
  const splitMap = (colorBy === 'split') ? buildSplitColorMap().map : null;

  let html = '<div class="overlap-popup">';
  html += '<div style="font-size:11px;color:#666;padding:4px 8px;border-bottom:1px solid #ddd"><b>'+indices.length+' episodes here</b> – pick one:</div>';
  indices.forEach(idx => {
    const ep = episodes[idx];
    const color = (colorBy === 'split')
      ? (splitMap[splitColorKey(ep, idx)] || DFLT)
      : (cm[ep[colorBy]] || DFLT);
    const bag = ep.rosbag_name ? ep.rosbag_name.split('/').pop() : '–';
    html += '<div class="overlap-item" onclick="pickFromOverlap('+idx+')">'
      +'<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:'+color+';margin-right:6px"></span>'
      +'<b>'+esc(ep.robot_id||'?')+'</b> ep '+(ep.episode_index??'?')
      +' · '+esc(ep.way_type||'–')
      +' · '+(ep.duration?ep.duration.toFixed(0)+'s':'–')
      +'<br><span style="font-size:10px;color:#888">'+esc(bag)+'</span>'
      +'</div>';
  });
  html += '</div>';

  L.popup({maxWidth:320,className:'overlap-popup-wrap'})
    .setLatLng(latlng)
    .setContent(html)
    .openOn(map);
}

function pickFromOverlap(idx){
  map.closePopup();
  // Find the layer
  let targetLayer = null;
  epLayer.eachLayer(l => {
    if(l._epIdx === idx){
      if(l.eachLayer){
        l.eachLayer(sub => { if(sub.getLatLngs && !targetLayer) targetLayer = sub; });
      } else targetLayer = l;
    }
  });
  selectEp(idx, targetLayer);
}


// ══════════════════════════════════════════════════════════════════════════
// SELECT EPISODE
// ══════════════════════════════════════════════════════════════════════════
function selectEp(idx, pl, opts={}){
  const { focusMap = true } = opts;
  if(selPolyline && selPolyline.setStyle) selPolyline.setStyle({weight:4,opacity:.75});
  if(pl && pl.setStyle) pl.setStyle({weight:7,opacity:1});
  selPolyline = pl;
  selectedEpisodeIdx = idx;

  const ep = episodes[idx];

  if(focusMap){
    if(pl && pl.getBounds) map.fitBounds(pl.getBounds(),{padding:[60,60],maxZoom:18});
    else if(pl && pl.getLatLng) map.setView(pl.getLatLng(),17);
  }

  showDetail(ep);
  updateEpisodeNavigator();
}


// ══════════════════════════════════════════════════════════════════════════
// DETAIL PANEL
// ══════════════════════════════════════════════════════════════════════════
function showDetail(ep){
  const panel = document.getElementById('detail-panel');
  const box   = document.getElementById('detail-content');
  panel.classList.remove('hidden');

  let h = '';
  if(ep.prompt) h += '<div class="detail-prompt">"'+esc(ep.prompt)+'"</div>';

  h += '<div class="detail-grid">';
  const bag = ep.rosbag_name ? ep.rosbag_name.split('/').pop() : '–';
  const pairs = [
    ['Robot', ep.robot_id],
    ['Episode', ep.episode_index],
    ['Way Type', ep.way_type],
    ['Surface', ep.surface],
    ['Weather', ep.weather],
    ['Time', ep.time_of_the_day],
    ['Fleet', ep.fleet_name],
    ['Project', ep.project],
    ['Duration', ep.duration ? ep.duration.toFixed(1)+'s' : '–'],
    ['GPS Points', (ep.gps_path||[]).length],
    ['Timestamp', ep.timestamp ? ep.timestamp.substring(0,19).replace('T',' ') : '–'],
    ['Rosbag', bag],
    ['Uploaded', ep.uploaded===true?'✅ Yes':ep.uploaded===false?'❌ No':'–'],
    ['UUID', ep.uuid || '–'],
  ];
  if(ep.movement_time) pairs.push(['Move Time', ep.movement_time.toFixed(1)+'s']);
  if(ep.supervisor_email) pairs.push(['Supervisor', ep.supervisor_email]);
  pairs.forEach(([l,v])=>{ h+='<div class="di"><div class="dl">'+l+'</div><div class="dv">'+(v??'–')+'</div></div>'; });
  h += '</div>';

  if(ep.image_path){
    h += '<img class="detail-img" src="'+esc(ep.image_path)+'" onerror="this.style.display=\'none\'" onclick="window.open(this.src,\'_blank\')" title="Click to enlarge">';
  }

  h += '<div class="detail-actions">';
  h += '<a href="#" onclick="toggleFocusForSelected();return false">🎯 Focus this rosbag</a>';
  if(ep.bucket_url) h += '<a href="'+esc(ep.bucket_url)+'" target="_blank">📂 Open Rosbag in GCS</a>';
  if(ep.mcap_path)  h += '<a href="#" onclick="copyText(\''+esc(ep.mcap_path)+'\',this);return false">📋 Copy MCAP path</a>';
  if(ep.image_path) h += '<a href="'+esc(ep.image_path)+'" target="_blank">🖼️ Open image</a>';
  // Copy gs:// path if we can reconstruct it
  if(!ep.mcap_path && ep.rosbag_name){
    const gsPath = 'gs://autonomy-vision/rosbags/fomo/'+bag+'/';
    h += '<a href="#" onclick="copyText(\''+esc(gsPath)+'\',this);return false">📋 Copy GCS path</a>';
  }
  h += '</div>';

  box.innerHTML = h;
  panel.scrollIntoView({behavior:'smooth',block:'start'});
}

function getEpisodeGroupKey(ep){
  if(!ep) return null;
  return ep.rosbag_name || ep.mcap_path || ep.uuid || null;
}

function getEpisodeGroupLabel(ep){
  if(!ep) return 'Unknown rosbag';
  if(ep.rosbag_name) return ep.rosbag_name.split('/').pop();
  if(ep.mcap_path) return ep.mcap_path.split('/').pop();
  return ep.uuid || 'Unknown rosbag';
}

function getSortedGroupIndices(groupKey){
  if(!groupKey) return [];
  const idxs = [];
  episodes.forEach((ep, idx) => {
    if(getEpisodeGroupKey(ep) === groupKey) idxs.push(idx);
  });
  idxs.sort((a,b) => {
    const ea = episodes[a], eb = episodes[b];
    const ai = Number(ea.episode_index), bi = Number(eb.episode_index);
    const aValid = Number.isFinite(ai), bValid = Number.isFinite(bi);
    if(aValid && bValid && ai !== bi) return ai - bi;
    if(aValid && !bValid) return -1;
    if(!aValid && bValid) return 1;
    const ta = ea.timestamp || '', tb = eb.timestamp || '';
    if(ta < tb) return -1;
    if(ta > tb) return 1;
    return a - b;
  });
  return idxs;
}

function updateEpisodeNavigator(){
  const nav = document.getElementById('episode-nav');
  const label = document.getElementById('episode-nav-label');
  const sel = document.getElementById('episode-nav-select');
  const prevBtn = document.getElementById('episode-prev-btn');
  const nextBtn = document.getElementById('episode-next-btn');
  const focusBtn = document.getElementById('focus-toggle-btn');

  if(selectedEpisodeIdx === null || !episodes[selectedEpisodeIdx]){
    nav.classList.add('hidden');
    updateFocusBar();
    return;
  }

  const current = episodes[selectedEpisodeIdx];
  const groupKey = getEpisodeGroupKey(current);
  const groupLabel = getEpisodeGroupLabel(current);
  const groupIndices = getSortedGroupIndices(groupKey);
  const pos = groupIndices.indexOf(selectedEpisodeIdx);

  nav.classList.remove('hidden');
  label.textContent = groupLabel + ' · episodio ' + (pos + 1) + ' de ' + groupIndices.length;
  focusBtn.textContent = focusedGroupKey === groupKey ? 'Unfocus rosbag' : 'Focus this rosbag';

  sel.innerHTML = '';
  groupIndices.forEach((idx, i) => {
    const ep = episodes[idx];
    const opt = document.createElement('option');
    opt.value = String(idx);
    const epNum = ep.episode_index ?? '?';
    const ts = ep.timestamp ? ep.timestamp.substring(0,16).replace('T',' ') : '–';
    opt.textContent = (i + 1) + '/' + groupIndices.length + ' · ep ' + epNum + ' · ' + ts;
    if(idx === selectedEpisodeIdx) opt.selected = true;
    sel.appendChild(opt);
  });

  prevBtn.disabled = (pos <= 0);
  nextBtn.disabled = (pos >= groupIndices.length - 1);
  updateFocusBar();
}

function jumpToEpisodeInGroup(rawIdx){
  const idx = Number(rawIdx);
  if(!Number.isInteger(idx) || idx < 0 || idx >= episodes.length) return;
  const pl = episodeMainLayerByIdx[idx];
  if(pl) selectEp(idx, pl);
}

function goEpisodeInGroup(step){
  if(selectedEpisodeIdx === null || !episodes[selectedEpisodeIdx]) return;
  const groupKey = getEpisodeGroupKey(episodes[selectedEpisodeIdx]);
  const groupIndices = getSortedGroupIndices(groupKey);
  const pos = groupIndices.indexOf(selectedEpisodeIdx);
  if(pos < 0) return;
  const nextPos = pos + step;
  if(nextPos < 0 || nextPos >= groupIndices.length) return;
  const idx = groupIndices[nextPos];
  const pl = episodeMainLayerByIdx[idx];
  if(pl) selectEp(idx, pl);
}

function isEpisodeVisible(ep){
  if(focusedGroupKey && getEpisodeGroupKey(ep) !== focusedGroupKey) return false;
  if(episodeIndexFilter.size > 0){
    const ei = ep.episode_index != null ? String(ep.episode_index) : null;
    if(ei === null || !episodeIndexFilter.has(ei)) return false;
  }
  return true;
}

function refreshEpisodeIndexFilter(){
  const section = document.getElementById('ep-idx-section');
  const wrap = document.getElementById('ep-idx-ms');

  // Determine the relevant episode pool
  let pool;
  if(focusedGroupKey){
    pool = episodes.filter(ep => getEpisodeGroupKey(ep) === focusedGroupKey);
  } else if(document.getElementById('f-rosbag').value.trim()){
    pool = episodes;
  } else {
    episodeIndexFilter.clear();
    section.classList.add('hidden');
    return;
  }

  // Distinct episode_index values sorted numerically
  const indices = [...new Set(
    pool.map(ep => ep.episode_index).filter(v => v != null).map(v => Number(v))
  )].sort((a,b)=>a-b).map(String);

  if(indices.length <= 1){
    episodeIndexFilter.clear();
    section.classList.add('hidden');
    return;
  }

  // Reset filter and rebuild dropdown
  episodeIndexFilter.clear();
  wrap.innerHTML = '';

  const btn = document.createElement('div');
  btn.className = 'ms-btn';
  btn.id = 'ep-idx-ms-btn';
  btn.innerHTML = '<span class="ms-text">All (' + indices.length + ')</span><span class="arrow">▼</span>';
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const dd = wrap.querySelector('.ms-dd');
    const wasOpen = dd.classList.contains('show');
    document.querySelectorAll('.ms-dd.show').forEach(d=>d.classList.remove('show'));
    document.querySelectorAll('.ms-btn.open').forEach(b=>b.classList.remove('open'));
    if(!wasOpen){ dd.classList.add('show'); btn.classList.add('open'); }
  });
  wrap.appendChild(btn);

  const dd = document.createElement('div');
  dd.className = 'ms-dd';

  const acts = document.createElement('div');
  acts.className = 'ms-actions';
  acts.innerHTML = '<button onclick="epIdxSelectAll()">All</button><button onclick="epIdxSelectNone()">None</button>';
  dd.appendChild(acts);

  indices.forEach(idxStr => {
    const ep = pool.find(e => String(e.episode_index) === idxStr);
    const ts = ep && ep.timestamp ? ep.timestamp.substring(0,16).replace('T',' ') : '';
    const dur = ep && ep.duration ? ep.duration.toFixed(1)+'s' : '';

    const lbl = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = idxStr;
    cb.className = 'ep-idx-cb';
    cb.addEventListener('change', () => {
      if(cb.checked) episodeIndexFilter.add(idxStr);
      else episodeIndexFilter.delete(idxStr);
      updateEpIdxDisplay();
      renderMap();
    });
    lbl.appendChild(cb);
    let txt = 'ep ' + idxStr;
    if(ts) txt += ' · ' + ts;
    if(dur) txt += ' · ' + dur;
    lbl.appendChild(document.createTextNode(txt));
    dd.appendChild(lbl);
  });
  wrap.appendChild(dd);

  section.classList.remove('hidden');
}

function epIdxSelectAll(){
  document.querySelectorAll('.ep-idx-cb').forEach(cb => { cb.checked = true; episodeIndexFilter.add(cb.value); });
  updateEpIdxDisplay();
  renderMap();
}

function epIdxSelectNone(){
  document.querySelectorAll('.ep-idx-cb').forEach(cb => { cb.checked = false; });
  episodeIndexFilter.clear();
  updateEpIdxDisplay();
  renderMap();
}

function updateEpIdxDisplay(){
  const btn = document.getElementById('ep-idx-ms-btn');
  if(!btn) return;
  const btnText = btn.querySelector('.ms-text');
  if(!btnText) return;
  const total = document.querySelectorAll('.ep-idx-cb').length;
  if(episodeIndexFilter.size === 0){
    btnText.textContent = 'All (' + total + ')';
  } else {
    btnText.textContent = episodeIndexFilter.size + ' / ' + total + ' selected';
  }
}

function toggleFocusForSelected(){
  if(selectedEpisodeIdx === null || !episodes[selectedEpisodeIdx]) return;
  const ep = episodes[selectedEpisodeIdx];
  const key = getEpisodeGroupKey(ep);
  if(!key) return;

  if(focusedGroupKey === key){
    clearEpisodeFocus();
    return;
  }

  focusedGroupKey = key;
  focusedGroupLabel = getEpisodeGroupLabel(ep);
  refreshEpisodeIndexFilter();
  renderMap();
}

function clearEpisodeFocus(){
  focusedGroupKey = null;
  focusedGroupLabel = null;
  refreshEpisodeIndexFilter();
  renderMap();
  updateFocusBar();
}

function updateFocusBar(){
  const bar = document.getElementById('focus-bar');
  const txt = document.getElementById('focus-bar-text');
  if(!focusedGroupKey){
    bar.classList.add('hidden');
    return;
  }
  const count = getSortedGroupIndices(focusedGroupKey).length;
  txt.textContent = 'Focused rosbag: ' + (focusedGroupLabel || 'current') + ' · ' + count + ' episodios';
  bar.classList.remove('hidden');
}


// ══════════════════════════════════════════════════════════════════════════
// AREA SELECTION (draw rectangle → query by bounding box)
// ══════════════════════════════════════════════════════════════════════════
let areaDrawer = null;
let activeBbox = null;  // Stores current bbox query

function toggleAreaSelect(){
  const btn = document.getElementById('btn-area');
  if(!areaMode){
    areaMode = true;
    btn.classList.add('active');
    btn.textContent = '✕ Cancel Area';
    document.getElementById('area-banner').classList.remove('hidden');
    drawnItems.clearLayers();
    areaDrawer = new L.Draw.Rectangle(map, {
      shapeOptions: {color:'#8b5cf6',weight:2,fillOpacity:.15}
    });
    areaDrawer.enable();
  } else {
    cancelAreaSelect();
  }
}

function cancelAreaSelect(){
  areaMode = false;
  const btn = document.getElementById('btn-area');
  btn.classList.remove('active');
  btn.textContent = '📐 Select Area';
  document.getElementById('area-banner').classList.add('hidden');
  if(areaDrawer){ areaDrawer.disable(); areaDrawer=null; }
}

function clearAreaSelection(){
  drawnItems.clearLayers(); activeBbox = null;
  document.getElementById('area-active-banner').classList.add('hidden');
}

function clearAreaAndReload(){
  clearAreaSelection();
  loadEpisodes();
}

function onAreaDrawn(e){
  if(!areaMode) return;

  // Keep the drawn rectangle visible on the map
  drawnItems.clearLayers();
  drawnItems.addLayer(e.layer);

  // Compute bounding box
  const bounds = e.layer.getBounds();
  const bbox = [
    bounds.getSouth(), bounds.getWest(),
    bounds.getNorth(), bounds.getEast()
  ].join(',');
  activeBbox = bbox;

  // Reset drawing mode (but keep rectangle visible!)
  areaMode = false;
  const btn = document.getElementById('btn-area');
  btn.classList.remove('active');
  btn.textContent = '📐 Select Area';
  document.getElementById('area-banner').classList.add('hidden');
  if(areaDrawer){ areaDrawer.disable(); areaDrawer=null; }

  // Show active area banner
  document.getElementById('area-active-banner').classList.remove('hidden');

  // RE-QUERY the backend with bbox filter
  console.log('[Area Select] Querying backend with bbox:', bbox);
  loadEpisodes(bbox);
}


// ══════════════════════════════════════════════════════════════════════════
// LEGEND
// ══════════════════════════════════════════════════════════════════════════
function renderLegend(){
  const key = document.getElementById('color-by').value;
  const isRandom = (key === 'random');
  const isSplit = (key === 'split');
  let h = '';

  if(isRandom){
    h += '<div class="legend-item" style="flex-direction:column;align-items:flex-start;gap:2px">'
      +'<span style="font-weight:600;font-size:11px">🎲 Random Colors</span>'
      +'<span style="color:#64748b;font-size:10px">Each episode gets a unique color for easy visual distinction</span>'
      +'</div>';
    h += '<div style="display:flex;flex-wrap:wrap;gap:2px;margin:4px 0">';
    RANDOM_POOL.slice(0,14).forEach(c => {
      h += '<span style="width:16px;height:6px;border-radius:2px;background:'+c+';display:inline-block"></span>';
    });
    h += '</div>';
  } else if(isSplit){
    const { map: sm, sorted } = buildSplitColorMap();
    h += '<div class="legend-item" style="flex-direction:column;align-items:flex-start;gap:2px">'
      +'<span style="font-weight:600;font-size:11px">Split colors</span>'
      +'<span style="color:#64748b;font-size:10px">'+sorted.length+' unique splits (rosbag + episode index)</span>'
      +'</div>';
    const maxShow = 20;
    sorted.slice(0, maxShow).forEach(k => {
      h += '<div class="legend-item"><span class="legend-swatch" style="background:'+sm[k]+'"></span>'
        + esc(splitKeyLabel(k)) + '</div>';
    });
    if(sorted.length > maxShow){
      h += '<div style="font-size:10px;color:#64748b;padding:2px 0">+ '+(sorted.length - maxShow)+' more…</div>';
    }
  } else {
    const cm = CMAPS[key]||{};
    // Count per category
    const counts = {};
    episodes.forEach(ep => {
      const val = ep[key] || 'Unknown';
      counts[val] = (counts[val]||0) + 1;
    });

    for(const [n,c] of Object.entries(cm)){
      const cnt = counts[n]||0;
      if(cnt > 0 || key!=='robot_id'){
        h += '<div class="legend-item"><span class="legend-swatch" style="background:'+c+'"></span>'
          +n.replace(/_/g,' ')+' <span style="color:#64748b;margin-left:auto;font-size:10px">'+cnt+'</span></div>';
      }
    }
    if(counts['Unknown']){
      h += '<div class="legend-item"><span class="legend-swatch" style="background:'+DFLT+'"></span>'
        +'Unknown <span style="color:#64748b;margin-left:auto;font-size:10px">'+counts['Unknown']+'</span></div>';
    }
  }

  const showSE = document.getElementById('toggle-start-end').checked;
  if(showSE){
    h += '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #1e293b">';
    h += '<div class="legend-item"><span class="legend-dot" style="background:#10b981"></span>Start</div>';
    h += '<div class="legend-item"><span class="legend-dot" style="background:#ef4444"></span>End</div>';
    h += '</div>';
  }
  document.getElementById('legend').innerHTML = h;
}


// ══════════════════════════════════════════════════════════════════════════
// FIT BOUNDS
// ══════════════════════════════════════════════════════════════════════════
function fitBounds(){
  const pts=[];
  episodes.forEach(e=>{
    if(e.initial_gps_point) pts.push(e.initial_gps_point);
    if(e.gps_path) e.gps_path.forEach(p=>pts.push(p));
  });
  if(pts.length) map.fitBounds(L.latLngBounds(pts),{padding:[30,30]});
}


// ══════════════════════════════════════════════════════════════════════════
// USB TOGGLE
// ══════════════════════════════════════════════════════════════════════════
async function toggleUsbs(){
  const on = document.getElementById('toggle-usbs').checked;
  if(on){
    if(!usbsLoaded){
      showLoader(true);
      try{
        const r = await fetch('/api/usbs');
        const bots = await r.json();
        bots.forEach(b=>{
          if(!b.lat||!b.lng) return;
          const pct = b.usb_size_gb ? ((b.usb_used_gb/b.usb_size_gb)*100).toFixed(0) : '?';
          const m = L.marker([b.lat,b.lng],{
            icon: L.divIcon({
              className:'',
              html:'<div style="background:#f59e0b;color:#000;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;white-space:nowrap;border:1px solid #fff">🤖 '+esc(b.bot_id)+'</div>',
              iconAnchor:[20,10]
            })
          });
          m.bindPopup('<b>'+esc(b.bot_id)+'</b><br>Campus: '+esc(b.campus_name||'?')
            +'<br>USB: '+((b.usb_used_gb||0).toFixed(1))+' / '+((b.usb_size_gb||0).toFixed(1))+' GB ('+pct+'%)'
            +'<br>Format: '+esc(b.usb_format||'?')
            +'<br>Last seen: '+(b.timestamp?b.timestamp.substring(0,19).replace('T',' '):'?'));
          usbLayer.addLayer(m);
        });
        usbsLoaded = true;
      }catch(e){ console.error('loadUsbs',e); }
      finally{ showLoader(false); }
    }
    map.addLayer(usbLayer);
  } else {
    map.removeLayer(usbLayer);
  }
}


// ══════════════════════════════════════════════════════════════════════════
// RESET
// ══════════════════════════════════════════════════════════════════════════
// ── Date presets ──
function setDatePreset(preset){
  const today=new Date();const fmt=d=>d.toISOString().split('T')[0];
  const toEl=document.getElementById('f-date-to');const fromEl=document.getElementById('f-date-from');
  toEl.value=fmt(today);
  if(preset==='today'){fromEl.value=fmt(today);}
  else if(preset==='7d'){const d=new Date(today);d.setDate(d.getDate()-7);fromEl.value=fmt(d);}
  else if(preset==='30d'){const d=new Date(today);d.setDate(d.getDate()-30);fromEl.value=fmt(d);}
  else if(preset==='90d'){const d=new Date(today);d.setDate(d.getDate()-90);fromEl.value=fmt(d);}
  else{fromEl.value='';toEl.value='';}
}

function resetFilters(){
  // Clear multi-selects
  for(const key of Object.keys(msState)){
    msSelectNone(key);
  }
  document.getElementById('f-dataset-index').value = '';
  document.getElementById('lookup-result').textContent = '';
  document.getElementById('toggle-live-sync').checked = false;
  toggleLiveSync();
  document.getElementById('f-limit').value = '1500';
  document.getElementById('f-prompt').value = '';
  document.getElementById('f-rosbag').value = '';
  document.getElementById('f-date-from').value = '';
  document.getElementById('f-date-to').value = '';
  document.getElementById('detail-panel').classList.add('hidden');
  document.getElementById('ep-idx-section').classList.add('hidden');
  episodeIndexFilter.clear();
  selectedEpisodeIdx = null;
  focusedGroupKey = null;
  focusedGroupLabel = null;
  clearAreaSelection();
  cancelAreaSelect();
  // Reset color-by to Way Type
  setColorBy('way_type', document.querySelector('.cc-btn[data-color="way_type"]'));
  loadEpisodes();
}


// ══════════════════════════════════════════════════════════════════════════
// UTIL
// ══════════════════════════════════════════════════════════════════════════
function showLoader(v){ document.getElementById('loader').classList.toggle('hidden',!v); }
function esc(s){ const d=document.createElement('div'); d.textContent=String(s); return d.innerHTML; }
function copyText(t,el){ navigator.clipboard.writeText(t); const o=el.textContent; el.textContent='✅ Copied!'; setTimeout(()=>el.textContent=o,1500); }
</script>
</body>
</html>
"""


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n\033[1;36m🗺️  FOMO Episodes Explorer v2\033[0m")
    print(f"   Open \033[4mhttp://localhost:{PORT}\033[0m in your browser\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
