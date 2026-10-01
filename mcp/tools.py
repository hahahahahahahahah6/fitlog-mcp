"""MCP tool definitions, annotations, and handlers for fitlog-mcp.

Tool annotations follow MCP spec 2025-11-25 (readOnlyHint / destructiveHint /
idempotentHint / openWorldHint).
"""

from __future__ import annotations

import datetime

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
        except (KeyError, TypeError, ValueError):
            return None, f"sets[{i}] needs integer 'reps' and numeric 'weight'."
        if reps <= 0 or weight < 0:
            return None, f"sets[{i}] needs reps > 0 and weight >= 0."
        clean.append({"reps": reps, "weight": weight})
    return clean, None


# ---------------------------------------------------------------- handlers

def _h_log_workout(store: Store, args: dict) -> tuple[str, bool]:
    exercise = args.get("exercise")
    if not exercise or not isinstance(exercise, str):
        return "Missing required argument: 'exercise' (string).", True
    sets, err = _validate_sets(args.get("sets"))
    if err:
        return err, True
    date = args.get("date")
    before = store.get_personal_records().get(exercise, {}).get("max_weight_kg", 0)
    store.log_workout(exercise.strip(), sets, date)
    lines = [
        f"Logged {exercise.strip()}: {len(sets)} sets"
        + (f" on {date}." if date else " today.")
    ]
    for s in sets:
        lines.append(f"  - {s['reps']} reps x {s['weight']} kg")
    new_max = max(s["weight"] for s in sets)
    if new_max > before:
        lines.append(f"New PR for {exercise.strip()}: {new_max} kg!")
    return "\n".join(lines), False


def _h_get_history(store: Store, args: dict) -> tuple[str, bool]:
    exercise = args.get("exercise")
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        return "'limit' must be an integer.", True
    rows = store.get_history(exercise, limit)
    if not rows:
        what = f" for {exercise}" if exercise else ""
        return f"No workouts logged{what} yet.", False
    lines = []
    for r in rows:
        sets = ", ".join(f"{s['reps']}x{s['weight']}kg" for s in r["sets"])
        lines.append(f"{r['date']} — {r['exercise']}: {sets}")
    return "\n".join(lines), False


def _h_get_personal_records(store: Store, args: dict) -> tuple[str, bool]:
    prs = store.get_personal_records()
    if not prs:
        return "No personal records yet. Log a workout first.", False
    lines = ["Personal records:"]
    for ex in sorted(prs):
        p = prs[ex]
        lines.append(
            f"  - {ex}: {p['max_weight_kg']} kg x {p['reps_at_max']} reps"
            f" (est. 1RM {p['estimated_1rm_kg']} kg) on {p['date']}"
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
    except (TypeError, ValueError):
        return "Missing required argument: 'grams' (number).", True
    if grams <= 0 or grams > 500:
        return "'grams' must be between 0 and 500.", True
    total = store.log_protein(grams, args.get("date"))
    lo, hi = NUTRITION_TARGETS["protein_g"]["min"], NUTRITION_TARGETS["protein_g"]["max"]
    return (
        f"Logged {grams:g} g protein. Total today: {total:g} g "
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
        "description": "Log a completed workout: an exercise with its sets of reps and weight in kg.",
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
                            "weight": {"type": "number", "description": "Weight in kg"},
                        },
                        "required": ["reps", "weight"],
                    },
                },
                "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"},
            },
            "required": ["exercise", "sets"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
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
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "plan_workout",
        "description": "Today's training plan from the stored 5-day split (or a given weekday).",
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
        "description": "The 5-day training split (back/chest/legs/arms/shoulders).",
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
