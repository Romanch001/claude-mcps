"""
Apple Health MCP Server — remote HTTP/SSE endpoint for claude.ai
Parses Apple Health export XML and provides health data analysis.
Also supports manual health metric logging and analysis.

No API key required. Users paste their Apple Health data as text.
To export: iPhone → Health app → Profile icon → Export All Health Data → export.zip → export.xml
"""
import os
import json
import math
from datetime import datetime, timedelta
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
import uvicorn

PORT = int(os.environ.get("PORT", 8000))
server = Server("apple-health-mcp")
sse_transport = SseServerTransport("/messages/")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="parse_health_records",
            description=(
                "Parse a snippet of Apple Health XML export data and extract health metrics. "
                "Paste a portion of your export.xml (from Apple Health Export All Data → export.zip)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "xml_snippet": {
                        "type": "string",
                        "description": (
                            "Paste XML content from Apple Health export.xml. "
                            "Can be a portion with <Record> elements. "
                            "Example: '<Record type=\"HKQuantityTypeIdentifierStepCount\" value=\"8432\" startDate=\"2024-01-15\"/>'"
                        )
                    },
                    "metric_filter": {
                        "type": "string",
                        "description": "Filter by metric type: 'steps', 'heart_rate', 'sleep', 'weight', 'calories', 'distance', 'blood_pressure', 'blood_glucose', 'vo2max'. Leave empty for all.",
                        "default": ""
                    }
                },
                "required": ["xml_snippet"]
            }
        ),
        Tool(
            name="analyze_health_data",
            description="Analyze a JSON array of health data points and provide insights, trends, and recommendations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "description": "Array of health data points with date, metric, and value fields.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string", "description": "Date in YYYY-MM-DD format."},
                                "metric": {"type": "string", "description": "Metric name, e.g. 'steps', 'heart_rate_bpm', 'weight_kg', 'sleep_hours'."},
                                "value": {"type": "number", "description": "Numeric value."}
                            },
                            "required": ["date", "metric", "value"]
                        }
                    }
                },
                "required": ["data"]
            }
        ),
        Tool(
            name="calculate_health_score",
            description="Calculate an overall health score based on key metrics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "avg_daily_steps": {"type": "number", "description": "Average daily step count."},
                    "avg_sleep_hours": {"type": "number", "description": "Average nightly sleep hours."},
                    "avg_resting_hr": {"type": "number", "description": "Average resting heart rate (bpm)."},
                    "age": {"type": "integer", "description": "Age in years."},
                    "bmi": {"type": "number", "description": "Body Mass Index (optional)."},
                    "avg_calories_active": {"type": "number", "description": "Average active calories burned per day (optional)."}
                },
                "required": ["avg_daily_steps", "avg_sleep_hours"]
            }
        ),
        Tool(
            name="get_health_reference_ranges",
            description="Get normal/healthy reference ranges for common health metrics by age and gender.",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "description": "Metric to look up: 'heart_rate', 'blood_pressure', 'bmi', 'blood_glucose', 'cholesterol', 'vo2max', 'sleep', 'steps'."},
                    "age": {"type": "integer", "description": "Age in years (optional, for age-specific ranges)."},
                    "gender": {"type": "string", "description": "'male' or 'female' (optional)."}
                },
                "required": ["metric"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "parse_health_records":
        result = _parse_xml(arguments.get("xml_snippet", ""), arguments.get("metric_filter", ""))
    elif name == "analyze_health_data":
        result = _analyze_data(arguments.get("data", []))
    elif name == "calculate_health_score":
        result = _health_score(arguments)
    elif name == "get_health_reference_ranges":
        result = _reference_ranges(arguments.get("metric", ""), arguments.get("age"), arguments.get("gender", ""))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [TextContent(type="text", text=result)]


def _parse_xml(xml: str, filter_type: str) -> str:
    import re
    # Map HK type identifiers to friendly names
    type_map = {
        "StepCount": "steps",
        "HeartRate": "heart_rate_bpm",
        "SleepAnalysis": "sleep",
        "BodyMass": "weight_kg",
        "ActiveEnergyBurned": "active_calories_kcal",
        "BasalEnergyBurned": "basal_calories_kcal",
        "DistanceWalkingRunning": "distance_km",
        "BloodPressureSystolic": "bp_systolic_mmhg",
        "BloodPressureDiastolic": "bp_diastolic_mmhg",
        "BloodGlucose": "blood_glucose_mmol",
        "VO2Max": "vo2max_ml_kg_min",
        "OxygenSaturation": "spo2_pct",
        "RespiratoryRate": "respiratory_rate_bpm",
        "BodyFatPercentage": "body_fat_pct",
        "Height": "height_m",
    }

    filter_keywords = {
        "steps": ["StepCount"],
        "heart_rate": ["HeartRate"],
        "sleep": ["Sleep"],
        "weight": ["BodyMass"],
        "calories": ["EnergyBurned"],
        "distance": ["Distance"],
        "blood_pressure": ["BloodPressure"],
        "blood_glucose": ["BloodGlucose"],
        "vo2max": ["VO2Max"],
    }

    records = re.findall(r'<Record[^>]+/>', xml, re.IGNORECASE)
    if not records:
        records = re.findall(r'<Record[^>]+>', xml, re.IGNORECASE)

    if not records:
        return (
            "No <Record> elements found in the provided XML.\n\n"
            "Make sure to paste content from Apple Health export.xml which contains lines like:\n"
            '<Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" '
            'unit="count" creationDate="2024-01-15" startDate="2024-01-15" endDate="2024-01-15" value="8432"/>'
        )

    parsed = []
    for rec in records:
        type_match = re.search(r'type="([^"]+)"', rec)
        value_match = re.search(r'value="([^"]+)"', rec)
        date_match = re.search(r'startDate="([^"]+)"', rec) or re.search(r'creationDate="([^"]+)"', rec)

        if not type_match or not value_match:
            continue

        full_type = type_match.group(1).replace("HKQuantityTypeIdentifier", "").replace("HKCategoryTypeIdentifier", "")
        friendly = type_map.get(full_type, full_type)
        value_str = value_match.group(1)
        date_str = date_match.group(1)[:10] if date_match else "unknown"

        if filter_type and filter_type.lower() not in friendly.lower():
            skip = True
            for kw in filter_keywords.get(filter_type.lower(), [filter_type]):
                if kw.lower() in full_type.lower():
                    skip = False
                    break
            if skip:
                continue

        try:
            val = float(value_str)
        except ValueError:
            val = value_str

        parsed.append({"date": date_str, "metric": friendly, "value": val})

    if not parsed:
        return f"No records matched filter '{filter_type}'. Found {len(records)} total records."

    # Group and summarize
    by_metric: dict = {}
    for p in parsed:
        m = p["metric"]
        if m not in by_metric:
            by_metric[m] = []
        try:
            by_metric[m].append(float(p["value"]))
        except (ValueError, TypeError):
            pass

    lines = [f"**Apple Health Records Parsed: {len(parsed)} entries**\n"]
    for metric, values in sorted(by_metric.items()):
        if not values:
            continue
        avg = sum(values) / len(values)
        mn, mx = min(values), max(values)
        lines.append(
            f"• **{metric}**: {len(values)} records | "
            f"avg={avg:.1f} | min={mn:.1f} | max={mx:.1f}"
        )
    return "\n".join(lines)


def _analyze_data(data: list) -> str:
    if not data:
        return "No data provided."
    by_metric: dict = {}
    for point in data:
        m = point.get("metric", "unknown")
        if m not in by_metric:
            by_metric[m] = []
        by_metric[m].append({"date": point.get("date", ""), "value": float(point.get("value", 0))})

    lines = ["**Health Data Analysis:**\n"]
    for metric, points in by_metric.items():
        values = [p["value"] for p in points]
        avg = sum(values) / len(values)
        std = math.sqrt(sum((v - avg)**2 for v in values) / len(values)) if len(values) > 1 else 0
        trend = "↗" if values[-1] > values[0] else ("↘" if values[-1] < values[0] else "→")
        lines.append(
            f"**{metric}** ({len(points)} data points):\n"
            f"  Average: {avg:.1f} | Std dev: {std:.1f} | Trend: {trend}\n"
            f"  Min: {min(values):.1f} | Max: {max(values):.1f}\n"
            f"  Latest: {values[-1]:.1f} (on {points[-1]['date']})"
        )

        # Simple health insights
        if "step" in metric.lower():
            if avg < 5000:
                lines.append("  ⚠️ Below recommended 7,500–10,000 steps/day")
            elif avg >= 10000:
                lines.append("  ✓ Excellent — above 10,000 steps/day target")
        elif "sleep" in metric.lower():
            if avg < 7:
                lines.append("  ⚠️ Below recommended 7-9 hours of sleep")
            elif avg > 9:
                lines.append("  ⚠️ Sleeping more than recommended — may indicate health issues")
            else:
                lines.append("  ✓ Sleep duration within healthy range (7-9 hours)")
        elif "heart_rate" in metric.lower() and "resting" not in metric.lower():
            if avg > 100:
                lines.append("  ⚠️ Average heart rate above 100 bpm — consult a doctor")
            elif avg < 60:
                lines.append("  ✓ Low resting heart rate — may indicate good cardiovascular fitness")
        lines.append("")

    return "\n".join(lines)


def _health_score(args) -> str:
    steps = float(args.get("avg_daily_steps", 0))
    sleep = float(args.get("avg_sleep_hours", 0))
    rhr = float(args.get("avg_resting_hr", 70))
    age = int(args.get("age", 35))
    bmi = args.get("bmi")
    active_cal = float(args.get("avg_calories_active", 300))

    # Score each component out of 100
    step_score = min(100, (steps / 10000) * 100)
    sleep_score = max(0, min(100, 100 - abs(sleep - 8) * 20))
    # Ideal resting HR: 50-70
    rhr_score = max(0, min(100, 100 - max(0, rhr - 70) * 2 - max(0, 60 - rhr) * 2))
    cal_score = min(100, (active_cal / 500) * 100)
    bmi_score = 75
    if bmi:
        bmi = float(bmi)
        if 18.5 <= bmi < 25:
            bmi_score = 100
        elif bmi < 18.5 or 25 <= bmi < 30:
            bmi_score = 65
        else:
            bmi_score = 40

    weights = {"steps": 0.30, "sleep": 0.30, "rhr": 0.20, "calories": 0.10, "bmi": 0.10}
    total = (
        step_score * weights["steps"] +
        sleep_score * weights["sleep"] +
        rhr_score * weights["rhr"] +
        cal_score * weights["calories"] +
        bmi_score * weights["bmi"]
    )

    grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D" if total >= 40 else "F"

    return (
        f"**Overall Health Score: {total:.0f}/100 (Grade: {grade})**\n\n"
        f"Component Scores:\n"
        f"  Steps ({steps:.0f}/day): {step_score:.0f}/100\n"
        f"  Sleep ({sleep:.1f}h/night): {sleep_score:.0f}/100\n"
        f"  Resting HR ({rhr:.0f} bpm): {rhr_score:.0f}/100\n"
        f"  Active Calories ({active_cal:.0f}/day): {cal_score:.0f}/100\n"
        f"  BMI ({bmi if bmi else 'not provided'}): {bmi_score:.0f}/100\n\n"
        f"**Recommendations:**\n"
        f"{'• Increase daily steps toward 10,000' if steps < 8000 else '• Keep up the great step count!'}\n"
        f"{'• Improve sleep duration to 7-9 hours' if sleep < 7 or sleep > 9 else '• Sleep duration is great!'}\n"
        f"{'• Consider cardio exercise to lower resting HR' if rhr > 80 else '• Resting heart rate looks good'}"
    )


def _reference_ranges(metric: str, age, gender: str) -> str:
    m = metric.lower()
    ranges = {
        "heart_rate": """\
**Heart Rate Reference Ranges:**
- Resting HR:
  • Athletes: 40–60 bpm
  • Normal adults: 60–100 bpm
  • Children (6-15 yrs): 70–100 bpm
- During exercise: 50–85% of max HR (max ≈ 220 − age)
- Concerning: > 100 bpm at rest (tachycardia) | < 60 bpm (bradycardia, unless athlete)
""",
        "blood_pressure": """\
**Blood Pressure Reference Ranges:**
- Normal:      < 120 / < 80 mmHg
- Elevated:    120–129 / < 80 mmHg
- High Stage 1: 130–139 / 80–89 mmHg
- High Stage 2: ≥ 140 / ≥ 90 mmHg
- Crisis:      > 180 / > 120 mmHg (seek emergency care)
- Low:         < 90 / < 60 mmHg (hypotension)
""",
        "bmi": """\
**BMI Reference Ranges (Adults):**
- Underweight: < 18.5
- Normal:      18.5 – 24.9
- Overweight:  25.0 – 29.9
- Obese Class I:  30.0 – 34.9
- Obese Class II: 35.0 – 39.9
- Obese Class III: ≥ 40.0

Note: BMI doesn't account for muscle mass, ethnicity, or body composition.
""",
        "blood_glucose": """\
**Blood Glucose Reference Ranges:**
- Fasting (normal):     70–99 mg/dL (3.9–5.5 mmol/L)
- Pre-diabetes:         100–125 mg/dL (5.6–6.9 mmol/L)
- Diabetes:             ≥ 126 mg/dL (≥ 7.0 mmol/L)
- Post-meal (2hr normal): < 140 mg/dL (< 7.8 mmol/L)
- HbA1c normal: < 5.7% | Pre-diabetes: 5.7–6.4% | Diabetes: ≥ 6.5%
""",
        "sleep": """\
**Sleep Duration Recommendations (National Sleep Foundation):**
- Newborns (0-3 mo): 14–17 hours
- Infants (4-11 mo): 12–15 hours
- Toddlers (1-2 yr): 11–14 hours
- Preschool (3-5): 10–13 hours
- School age (6-13): 9–11 hours
- Teenagers (14-17): 8–10 hours
- Adults (18-64):   7–9 hours
- Older adults (65+): 7–8 hours
""",
        "steps": """\
**Daily Step Count Reference:**
- Sedentary: < 5,000 steps
- Low active: 5,000–7,499
- Somewhat active: 7,500–9,999
- Active: 10,000–12,499
- Highly active: ≥ 12,500

WHO recommends 150 min moderate activity OR 75 min vigorous activity per week.
10,000 steps ≈ ~8 km ≈ ~400 active calories burned.
""",
        "vo2max": f"""\
**VO2 Max Reference Ranges (ml/kg/min):**
{'Male' if 'male' in gender.lower() else 'Female' if 'female' in gender.lower() else 'General'} reference:
- Excellent: > 52 (men) / > 45 (women)
- Good:      43–52 (men) / 38–45 (women)
- Fair:      34–42 (men) / 31–37 (women)
- Poor:      < 34 (men) / < 31 (women)

VO2 Max typically declines ~1% per year after age 25 without training.
""",
    }
    for key, ref in ranges.items():
        if key in m:
            return ref
    return f"Reference ranges for '{metric}' not in database. Common metrics: heart_rate, blood_pressure, bmi, blood_glucose, sleep, steps, vo2max, cholesterol."


async def handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "apple-health-mcp"})


app = Starlette(
    routes=[
        Route("/health", health),
        Route("/sse", handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
