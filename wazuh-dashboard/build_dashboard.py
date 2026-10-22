#!/usr/bin/env python3
"""Generate an importable saved-objects NDJSON for a Wazuh SOC triage dashboard.

Import via Dashboards Management > Saved objects > Import.
Assumes the Wazuh-managed index pattern with id "wazuh-alerts-*" already exists.
"""
import json
import sys
from pathlib import Path

INDEX_PATTERN_ID = "wazuh-alerts-*"
TIME_FIELD = "timestamp"
ID_PREFIX = "msent-"
OUT_FILE = Path(__file__).with_name("soc-triage-dashboard.ndjson")

# (label, KQL, color). Wazuh levels are 0-15; 15 is the maximum.
SEVERITIES = [
    ("Critical (15)", "rule.level >= 15", "#BF1B00"),
    ("High (12-14)", "rule.level >= 12 and rule.level <= 14", "#E7664C"),
    ("Medium (7-11)", "rule.level >= 7 and rule.level <= 11", "#F2C96D"),
    ("Low (0-6)", "rule.level >= 0 and rule.level <= 6", "#6092C0"),
]

LAST_24H = f'{TIME_FIELD} >= "now-24h"'
SRC_REF = "kibanaSavedObjectMeta.searchSourceJSON.index"

objects = []
panels = []


def search_source(query):
    return json.dumps({
        "query": {"query": query, "language": "kuery"},
        "filter": [],
        "indexRefName": SRC_REF,
    })


def index_ref():
    return [{"name": SRC_REF, "type": "index-pattern", "id": INDEX_PATTERN_ID}]


def add_vis(slug, title, vis_state, query="", ui_state=None):
    objects.append({
        "id": ID_PREFIX + slug,
        "type": "visualization",
        "attributes": {
            "title": title,
            "visState": json.dumps(vis_state),
            "uiStateJSON": json.dumps(ui_state or {}),
            "description": "",
            "version": 1,
            "kibanaSavedObjectMeta": {"searchSourceJSON": search_source(query)},
        },
        "references": index_ref(),
        "migrationVersion": {"visualization": "7.10.0"},
    })


def add_panel(slug, ptype, x, y, w, h):
    panels.append((ID_PREFIX + slug, ptype, {"x": x, "y": y, "w": w, "h": h}))


def agg(id_, type_, schema, params=None):
    return {"id": id_, "enabled": True, "type": type_, "schema": schema, "params": params or {}}


def terms(id_, field, size, order_by="1", schema="bucket", label=None):
    params = {
        "field": field, "orderBy": order_by, "order": "desc", "size": size,
        "otherBucket": False, "otherBucketLabel": "Other",
        "missingBucket": False, "missingBucketLabel": "Missing",
    }
    if label:
        params["customLabel"] = label
    return agg(id_, "terms", schema, params)


def metric_vis(title, aggs, ranges=None):
    # ranges: None for a plain tile; otherwise [(from, to), ...] coloured green -> red
    metric = {
        "percentageMode": False,
        "useRanges": bool(ranges),
        "colorSchema": "Green to Red",
        "metricColorMode": "Background" if ranges else "None",
        "colorsRange": [{"from": a, "to": b} for a, b in (ranges or [(0, 10000000)])],
        "labels": {"show": True},
        "invertColors": False,
        "style": {"bgFill": "#000", "bgColor": bool(ranges), "labelColor": False,
                  "subText": "", "fontSize": 52},
    }
    return {"title": title, "type": "metric", "aggs": aggs,
            "params": {"addTooltip": True, "addLegend": False, "type": "metric", "metric": metric}}


def stacked_bars_vis(title, interval):
    return {
        "title": title,
        "type": "histogram",
        "aggs": [
            agg("1", "count", "metric"),
            agg("2", "date_histogram", "segment", {
                "field": TIME_FIELD, "useNormalizedOpenSearchInterval": True,
                "scaleMetricValues": False, "interval": interval, "drop_partials": False,
                "min_doc_count": 0, "extended_bounds": {},
            }),
            agg("3", "filters", "group", {"filters": [
                {"input": {"query": q, "language": "kuery"}, "label": label}
                for label, q, _ in SEVERITIES
            ]}),
        ],
        "params": {
            "type": "histogram",
            "grid": {"categoryLines": False},
            "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom",
                              "show": True, "style": {}, "scale": {"type": "linear"},
                              "labels": {"show": True, "filter": True, "truncate": 100}, "title": {}}],
            "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
                           "position": "left", "show": True, "style": {},
                           "scale": {"type": "linear", "mode": "normal"},
                           "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                           "title": {"text": "Alerts"}}],
            "seriesParams": [{"show": True, "type": "histogram", "mode": "stacked",
                              "data": {"label": "Count", "id": "1"}, "valueAxis": "ValueAxis-1",
                              "drawLinesBetweenPoints": True, "showCircles": True}],
            "addTooltip": True, "addLegend": True, "legendPosition": "bottom",
            "times": [], "addTimeMarker": False, "labels": {"show": False},
            "thresholdLine": {"show": False, "value": 10, "width": 1, "style": "full", "color": "#E7664C"},
        },
    }


def table_vis(title, aggs, per_page=10):
    return {"title": title, "type": "table", "aggs": aggs,
            "params": {"perPage": per_page, "showPartialRows": False,
                       "showMetricsAtAllLevels": False,
                       "sort": {"columnIndex": None, "direction": None},
                       "showTotal": False, "totalFunc": "sum", "percentageCol": ""}}


severity_colors = {"vis": {"colors": {label: color for label, _, color in SEVERITIES}}}

# --- Banner: 24h counts -----------------------------------------------------
tiles = [
    ("total-24h", "Alerts (24h)", LAST_24H, None),
    ("crit-24h", "Critical (15) - 24h", f"{LAST_24H} and {SEVERITIES[0][1]}", [(0, 1), (1, 10000000)]),
    ("high-24h", "High (12-14) - 24h", f"{LAST_24H} and ({SEVERITIES[1][1]})", [(0, 1), (1, 10000000)]),
    ("med-24h", "Medium (7-11) - 24h", f"{LAST_24H} and ({SEVERITIES[2][1]})", None),
    ("low-24h", "Low (0-6) - 24h", f"{LAST_24H} and ({SEVERITIES[3][1]})", None),
]
for i, (slug, title, query, ranges) in enumerate(tiles):
    add_vis(slug, title, metric_vis(title, [agg("1", "count", "metric")], ranges), query)
    add_panel(slug, "visualization", i * 8, 0, 8, 8)

add_vis("agents-24h", "Agents reporting (24h)",
        metric_vis("Agents reporting (24h)",
                   [agg("1", "cardinality", "metric", {"field": "agent.id", "customLabel": "Unique agents"})]),
        LAST_24H)
add_panel("agents-24h", "visualization", 40, 0, 8, 8)

# --- Triage: high/critical alerts, newest first -----------------------------
triage_query = f"{LAST_24H} and rule.level >= 12"
objects.append({
    "id": ID_PREFIX + "triage-search",
    "type": "search",
    "attributes": {
        "title": "Action queue - level 12+ (24h)",
        "description": "Critical and high Wazuh alerts, newest first.",
        "hits": 0,
        "columns": ["agent.name", "rule.level", "rule.id", "rule.description", "data.srcip"],
        "sort": [[TIME_FIELD, "desc"]],
        "version": 1,
        "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps({
            "highlightAll": True, "version": True,
            "query": {"query": triage_query, "language": "kuery"},
            "filter": [], "indexRefName": SRC_REF,
        })},
    },
    "references": index_ref(),
    "migrationVersion": {"search": "7.9.3"},
})
add_panel("triage-search", "search", 0, 8, 48, 18)

# --- Trends -------------------------------------------------------------------
add_vis("hourly-24h", "Alerts per hour by severity (24h)",
        stacked_bars_vis("Alerts per hour by severity (24h)", "h"), LAST_24H, severity_colors)
add_panel("hourly-24h", "visualization", 0, 26, 24, 14)

add_vis("daily-7d", "Alerts per day by severity (7d)",
        stacked_bars_vis("Alerts per day by severity (7d)", "d"), f'{TIME_FIELD} >= "now-7d"', severity_colors)
add_panel("daily-7d", "visualization", 24, 26, 24, 14)

# --- Breakdown / tuning -------------------------------------------------------
add_vis("sev-table-7d", "Severity breakdown (7d)",
        table_vis("Severity breakdown (7d)", [
            agg("1", "count", "metric"),
            agg("2", "filters", "bucket", {"filters": [
                {"input": {"query": q, "language": "kuery"}, "label": label} for label, q, _ in SEVERITIES]}),
            agg("3", "cardinality", "metric", {"field": "agent.id", "customLabel": "Agents"}),
            agg("4", "cardinality", "metric", {"field": "rule.id", "customLabel": "Distinct rules"}),
        ]), f'{TIME_FIELD} >= "now-7d"')
add_panel("sev-table-7d", "visualization", 0, 40, 12, 14)

add_vis("top-agents-24h", "Top agents by alerts (24h)",
        table_vis("Top agents by alerts (24h)", [
            agg("1", "count", "metric"),
            agg("3", "max", "metric", {"field": "rule.level", "customLabel": "Max level"}),
            terms("2", "agent.name", 15),
        ]), LAST_24H)
add_panel("top-agents-24h", "visualization", 12, 40, 12, 14)

add_vis("top-rules-24h", "Top rules by volume (24h) - tuning",
        table_vis("Top rules by volume (24h) - tuning", [
            agg("1", "count", "metric"),
            agg("4", "max", "metric", {"field": "rule.level", "customLabel": "Level"}),
            agg("5", "cardinality", "metric", {"field": "agent.id", "customLabel": "Agents"}),
            terms("2", "rule.id", 20),
            terms("3", "rule.description", 1, label="Description"),
        ], per_page=20), LAST_24H)
add_panel("top-rules-24h", "visualization", 24, 40, 24, 14)

# --- Dashboard ----------------------------------------------------------------
objects.append({
    "id": ID_PREFIX + "dashboard",
    "type": "dashboard",
    "attributes": {
        "title": "SOC Triage",
        "description": "24h severity banner, action queue, 7d trend, tuning tables. "
                       "24h panels carry their own time filter; dashboard range should stay at 7d.",
        "hits": 0,
        "panelsJSON": json.dumps([
            {"version": "2.0.0", "gridData": {**grid, "i": str(n)}, "panelIndex": str(n),
             "embeddableConfig": {}, "panelRefName": f"panel_{n}"}
            for n, (_, _, grid) in enumerate(panels)
        ]),
        "optionsJSON": json.dumps({"hidePanelTitles": False, "useMargins": True}),
        "version": 1,
        "timeRestore": True,
        "timeTo": "now",
        "timeFrom": "now-7d",
        "refreshInterval": {"pause": False, "value": 300000},
        "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
            {"query": {"query": "", "language": "kuery"}, "filter": []})},
    },
    "references": [{"name": f"panel_{n}", "type": ptype, "id": pid}
                   for n, (pid, ptype, _) in enumerate(panels)],
    "migrationVersion": {"dashboard": "7.9.3"},
})

# --- Sanity checks + write ------------------------------------------------------
ids = {(o["type"], o["id"]) for o in objects}
assert len(ids) == len(objects), "duplicate saved object id"
for o in objects:
    for r in o["references"]:
        if r["type"] != "index-pattern":
            assert (r["type"], r["id"]) in ids, f"dangling reference {r}"

with OUT_FILE.open("w") as f:
    for o in objects:
        f.write(json.dumps(o) + "\n")
print(f"wrote {len(objects)} objects to {OUT_FILE}", file=sys.stderr)
