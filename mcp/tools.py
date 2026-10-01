"""MCP tool definitions, annotations, and handlers for fitlog-mcp.

Tool annotations follow MCP spec 2025-11-25 (readOnlyHint / destructiveHint /
idempotentHint / openWorldHint).
"""

from __future__ import annotations

import datetime
import math
import re

from store import NUTRITION_TARGETS, PROGRAM, Store

DAY_ALIASES = {
    "mon": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}

LB_TO_KG = 0.45359237
KG_TO_LB = 1.0 / LB_TO_KG

# Scope required to call each tool. Read-only tools need fitlog.read;
# tools that write data need fitlog.write.
TOOL_SCOPES = {
    "log_workout": "fitlog.write",
    "get_history": "fitlog.read",
    "get_personal_records": "fitlog.read",
    "plan_workout": "fitlog.read",
    "log_protein": "fitlog.write",
    "get_nutrition_targets": "fitlog.read",
}

# Scopes required to read each resource (all read-only).
RESOURCE_SCOPES = {
    "program://current": "fitlog.read",
    "pr://all": "fitlog.read",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_exercise(name: str) -> str:
    """Collapse whitespace: voice input yields ' Bench Press', 'bench  press'."""
    return " ".join(name.split())


def _validate_date(value, field: str = "date") -> tuple[str | None, str | None]:
    """Return (clean_date_or_None, error). Rejects 'yesterday' etc."""
    if value is None:
        return None, None
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None, f"'{field}' must be YYYY-MM-DD (got {value!r})."
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return None, f"'{field}' must be a real calendar date (got {value!r})."
    return value, None


def _resolve_day(day: str | None) -> str | None:
    if not day:
        return datetime.date.today().strftime("%A").lower()
    key = day.strip().lower()
    if key in PROGRAM:
        return key
    return DAY_ALIASES.get(key)


def _validate_sets(sets) -> tuple[list[dict] | None, str | None]:
    if not isinstance(sets, list) or not sets:
        return None, "'sets' must be a non-empty array of {reps, weight} objects."
    clean = []
    for i, s in enumerate(sets):
        if not isinstance(s, dict):
            return None, f"sets[{i}] must be an object with 'reps' and 'weight'."
        try:
            reps = int(s["reps"])
            weight = float(s["weight"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, f"sets[{i}] needs integer 'reps' and numeric 'weight'."
        # Reject NaN / Infinity: float("1e309") is inf and would otherwise
        # sail through and print "New PR: inf kg!".
        if not math.isfinite(weight):
            return None, f"sets[{i}].weight must be a finite number."
        if isinstance(reps, bool) or reps <= 0 or weight < 0:
            return None, f"sets[{i}] needs reps > 0 and weight >= 0."
        clean.append({"reps": reps, "weight": weight})
    return clean, None


def _epley(weight_kg: float, reps: int) -> float:
    return round(weight_kg * (1 + reps / 30.0), 1)


def _display_weight(weight_kg: float, unit: str) -> str:
    """Format a kg weight in the user's unit. Never silently converts:
    the caller passes the unit the user actually used ('kg' or 'lb').
    lb values are rounded to 1 decimal (0.1 lb < 0.01 kg storage precision)."""
    if unit.startswith("lb"):
        return f"{round(weight_kg * KG_TO_LB, 1):g} lb"
    return f"{weight_kg:g} kg"


def _parse_unit(args: dict) -> tuple[str, str | None]:
    """Return (normalized_unit, error). Normalized is 'kg' or 'lb'."""
    unit = str(args.get("unit") or "kg").strip().lower()
    if unit in ("lb", "lbs"):
        return "lb", None
    if unit == "kg":
        return "kg", None
    return "kg", "'unit' must be 'kg' or 'lb'."


# ---------------------------------------------------------------- handlers

def _h_log_workout(store: Store, args: dict) -> tuple[str, bool]:
    exercise = args.get("exercise")
    if not exercise or not isinstance(exercise, str) or not exercise.strip():
        return "Missing required argument: 'exercise' (string).", True
    exercise = _normalize_exercise(exercise)

    unit, err = _parse_unit(args)
    if err:
        return err, True
    to_kg = LB_TO_KG if unit == "lb" else 1.0

    sets, err = _validate_sets(args.get("sets"))
    if err:
        return err, True
    date, err = _validate_date(args.get("date"))
    if err:
        return err, True

    sets_kg = [
        {"reps": s["reps"], "weight": round(s["weight"] * to_kg, 2)} for s in sets
    ]

    before = store.get_personal_records().get(exercise.lower(), {})
    before_max = before.get("max_weight_kg", 0.0)
    before_1rm = before.get("estimated_1rm_kg", 0.0)

    store.log_workout(exercise, sets_kg, date)

    after = store.get_personal_records().get(exercise.lower(), {})
    lines = [
        f"Logged {exercise}: {len(sets_kg)} sets"
        + (f" on {date}." if date else " today.")
    ]
    for raw, conv in zip(sets, sets_kg):
        # Echo the weight in the unit the user gave; the kg equivalent is
        # parenthetical, never a replacement.
        if unit == "kg":
            lines.append(f"  - {raw['reps']} reps x {raw['weight']:g} kg")
        else:
            lines.append(
                f"  - {raw['reps']} reps x {raw['weight']:g} lb"
            )
    if after.get("max_weight_kg", 0.0) > before_max:
        # The new max comes from the sets just logged: report it in the
        # exact unit/weight the user gave, not a kg round-trip.
        new_max = max(s["weight"] for s in sets)
        lines.append(f"New PR for {exercise}: {new_max:g} {unit}!")
    if after.get("estimated_1rm_kg", 0.0) > before_1rm:
        lines.append(
            f"New estimated 1RM for {exercise}: "
            f"{_display_weight(after['estimated_1rm_kg'], unit)}!"
        )
    return "\n".join(lines), False


def _h_get_history(store: Store, args: dict) -> tuple[str, bool]:
    exercise = args.get("exercise")
    if exercise is not None:
        if not isinstance(exercise, str) or not exercise.strip():
            return "'exercise' must be a non-empty string.", True
        exercise = _normalize_exercise(exercise)
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        return "'limit' must be an integer.", True
    unit, err = _parse_unit(args)
    if err:
        return err, True
    rows = store.get_history(exercise, limit)
    if not rows:
        what = f" for {exercise}" if exercise else ""
        return f"No workouts logged{what} yet.", False
    lines = []
    for r in rows:
        sets = ", ".join(
            f"{s['reps']}x{_display_weight(s['weight'], unit)}" for s in r["sets"]
        )
        lines.append(f"{r['date']} — {r['exercise']}: {sets}")
    return "\n".join(lines), False


def _h_get_personal_records(store: Store, args: dict) -> tuple[str, bool]:
    unit, err = _parse_unit(args)
    if err:
        return err, True
    prs = store.get_personal_records()
    if not prs:
        return "No personal records yet. Log a workout first.", False
    lines = ["Personal records:"]
    for key in sorted(prs):
        p = prs[key]
        lines.append(
            f"  - {p['display']}: "
            f"{_display_weight(p['max_weight_kg'], unit)}"
            f" x {p['reps_at_max']} reps"
            f" (est. 1RM {_display_weight(p['estimated_1rm_kg'], unit)})"
        )
    return "\n".join(lines), False


def _h_plan_workout(store: Store, args: dict) -> tuple[str, bool]:
    day = _resolve_day(args.get("day"))
    if day is None:
        return (
            "'day' must be a weekday name (e.g. 'monday') or omitted for today.",
            True,
        )
    plan = PROGRAM[day]
    lines = [f"{day.capitalize()} — {plan['focus']} day:"]
    if not plan["exercises"]:
        lines.append("  Rest. Recover, hydrate, hit your protein target.")
    for e in plan["exercises"]:
        lines.append(f"  - {e['name']}: {e['sets']} sets x {e['reps']}")
    return "\n".join(lines), False


def _h_log_protein(store: Store, args: dict) -> tuple[str, bool]:
    try:
        grams = float(args.get("grams"))
    except (TypeError, ValueError, OverflowError):
        return "Missing required argument: 'grams' (number).", True
    if not math.isfinite(grams) or grams <= 0 or grams > 500:
        return "'grams' must be a finite number between 0 and 500.", True
    date, err = _validate_date(args.get("date"))
    if err:
        return err, True
    total = store.log_protein(grams, date)
    lo, hi = NUTRITION_TARGETS["protein_g"]["min"], NUTRITION_TARGETS["protein_g"]["max"]
    today = datetime.date.today().isoformat()
    label = "Total today" if (date is None or date == today) else f"Total on {date}"
    return (
        f"Logged {grams:g} g protein. {label}: {total:g} g "
        f"(target {lo}-{hi} g).",
        False,
    )


def _h_get_nutrition_targets(store: Store, args: dict) -> tuple[str, bool]:
    total = store.get_protein_total()
    lo, hi = NUTRITION_TARGETS["protein_g"]["min"], NUTRITION_TARGETS["protein_g"]["max"]
    status = "on track" if total >= lo else f"{lo - total:g} g short of the minimum"
    return (
        f"Daily targets: protein {lo}-{hi} g, creatine "
        f"{NUTRITION_TARGETS['creatine_g_per_day']} g.\n"
        f"Logged today: {total:g} g protein — {status}.",
        False,
    )


_HANDLERS = {
    "log_workout": _h_log_workout,
    "get_history": _h_get_history,
    "get_personal_records": _h_get_personal_records,
    "plan_workout": _h_plan_workout,
    "log_protein": _h_log_protein,
    "get_nutrition_targets": _h_get_nutrition_targets,
}


def call_tool(store: Store, name: str, arguments: dict | None) -> tuple[str, bool]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}", True
    return handler(store, arguments or {})


# ---------------------------------------------------------------- definitions

TOOL_DEFINITIONS = [
    {
        "name": "log_workout",
        "description": (
            "Log a completed workout: an exercise with its sets of reps and "
            "weight. Weight unit is kg by default; pass unit 'lb' for pounds. "
            "Date is YYYY-MM-DD, defaults to today."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "exercise": {"type": "string", "description": "Exercise name, e.g. 'Bench Press'"},
                "sets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "reps": {"type": "integer"},
                            "weight": {"type": "number", "description": "Weight per set"},
                        },
                        "required": ["reps", "weight"],
                    },
                },
                "unit": {
                    "type": "string",
                    "enum": ["kg", "lb"],
                    "default": "kg",
                    "description": "Weight unit for the sets",
                },
                "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            },
            "required": ["exercise", "sets"],
        },
        "annotations": {
            # Append-only logging: nothing is deleted or overwritten.
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_history",
        "description": "Show past workout sessions, optionally filtered by exercise.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "exercise": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "unit": {
                    "type": "string",
                    "enum": ["kg", "lb"],
                    "default": "kg",
                    "description": "Weight unit for display",
                },
            },
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_personal_records",
        "description": "Current personal records per lift: heaviest set and estimated 1RM.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "unit": {
                    "type": "string",
                    "enum": ["kg", "lb"],
                    "default": "kg",
                    "description": "Weight unit for display",
                },
            },
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "plan_workout",
        "description": "Today's training plan from the built-in 5-day split (or a given weekday).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "day": {"type": "string", "description": "Weekday name, defaults to today"},
            },
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "log_protein",
        "description": "Log protein intake in grams toward the 160-190 g daily target.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "grams": {"type": "number"},
                "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            },
            "required": ["grams"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_nutrition_targets",
        "description": "Daily protein/creatine targets and how much protein is logged today.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]

RESOURCE_DEFINITIONS = [
    {
        "uri": "program://current",
        "name": "Current training program",
        "description": "The built-in 5-day training split (back/chest/legs/arms/shoulders).",
        "mimeType": "application/json",
    },
    {
        "uri": "pr://all",
        "name": "Personal records",
        "description": "Personal records table across all lifts.",
        "mimeType": "application/json",
    },
]


def read_resource(store: Store, uri: str) -> tuple[str, bool]:
    import json as _json

    if uri == "program://current":
        return _json.dumps(PROGRAM, indent=2), False
    if uri == "pr://all":
        return _json.dumps(store.get_personal_records(), indent=2), False
    return f"Unknown resource: {uri}", True
