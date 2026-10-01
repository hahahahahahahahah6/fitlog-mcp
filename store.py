"""SQLite-backed store for workouts, personal records, and nutrition.

Stdlib only (sqlite3). Thread-safe via an internal lock because the HTTP
server handles each connection on its own thread.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading

DEFAULT_DB = os.path.expanduser("~/.fitlog/fitlog.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise TEXT NOT NULL,
    performed_at TEXT NOT NULL,
    sets_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS protein_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    grams REAL NOT NULL,
    logged_at TEXT NOT NULL
);
"""

# 5-day training split (back / chest / legs / arms / shoulders).
PROGRAM = {
    "monday": {
        "focus": "Back",
        "exercises": [
            {"name": "Pull-ups", "sets": "4", "reps": "8-10"},
            {"name": "Barbell Row", "sets": "4", "reps": "8-12"},
            {"name": "Lat Pulldown", "sets": "3", "reps": "10-12"},
            {"name": "Face Pulls", "sets": "3", "reps": "12-15"},
        ],
    },
    "tuesday": {
        "focus": "Chest",
        "exercises": [
            {"name": "Bench Press", "sets": "4", "reps": "6-8"},
            {"name": "Incline Dumbbell Press", "sets": "3", "reps": "8-12"},
            {"name": "Cable Fly", "sets": "3", "reps": "12-15"},
            {"name": "Dips", "sets": "3", "reps": "8-12"},
        ],
    },
    "wednesday": {
        "focus": "Legs",
        "exercises": [
            {"name": "Squat", "sets": "4", "reps": "6-8"},
            {"name": "Romanian Deadlift", "sets": "3", "reps": "8-12"},
            {"name": "Leg Press", "sets": "3", "reps": "10-12"},
            {"name": "Standing Calf Raise", "sets": "4", "reps": "12-15"},
        ],
    },
    "thursday": {
        "focus": "Arms",
        "exercises": [
            {"name": "Barbell Curl", "sets": "4", "reps": "8-12"},
            {"name": "Triceps Pushdown", "sets": "4", "reps": "10-12"},
            {"name": "Hammer Curl", "sets": "3", "reps": "10-12"},
            {"name": "Overhead Triceps Extension", "sets": "3", "reps": "10-12"},
        ],
    },
    "friday": {
        "focus": "Shoulders",
        "exercises": [
            {"name": "Overhead Press", "sets": "4", "reps": "6-8"},
            {"name": "Lateral Raise", "sets": "4", "reps": "12-15"},
            {"name": "Rear Delt Fly", "sets": "3", "reps": "12-15"},
            {"name": "Shrugs", "sets": "3", "reps": "10-12"},
        ],
    },
    "saturday": {
        "focus": "Rest / Cardio",
        "exercises": [
            {"name": "Stair climber", "sets": "1", "reps": "20-30 min"},
        ],
    },
    "sunday": {"focus": "Rest", "exercises": []},
}

NUTRITION_TARGETS = {
    "protein_g": {"min": 160, "max": 190},
    "creatine_g_per_day": 5,
    "notes": "Protein target 160-190 g/day; 5 g creatine daily.",
}


def _today() -> str:
    return datetime.date.today().isoformat()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("FITLOG_DB", DEFAULT_DB)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False because the HTTP server is threaded;
        # all access is serialized with the lock.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- workouts ------------------------------------------------------

    def log_workout(
        self,
        exercise: str,
        sets: list[dict],
        performed_at: str | None = None,
    ) -> int:
        exercise = " ".join(exercise.split())  # normalize whitespace
        day = performed_at or _today()
        payload = json.dumps(sets)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO workouts (exercise, performed_at, sets_json)"
                " VALUES (?, ?, ?)",
                (exercise, day, payload),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_history(self, exercise: str | None = None, limit: int = 10) -> list[dict]:
        limit = max(1, min(100, int(limit)))
        with self._lock:
            if exercise:
                # Case- and whitespace-insensitive: "bench press",
                # "Bench Press" and "  BENCH PRESS " are one lift.
                rows = self._conn.execute(
                    "SELECT id, exercise, performed_at, sets_json FROM workouts"
                    " WHERE lower(exercise) = lower(?)"
                    " ORDER BY performed_at DESC, id DESC LIMIT ?",
                    (" ".join(exercise.split()), limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, exercise, performed_at, sets_json FROM workouts"
                    " ORDER BY performed_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "id": r["id"],
                "exercise": r["exercise"],
                "date": r["performed_at"],
                "sets": json.loads(r["sets_json"]),
            }
            for r in rows
        ]

    def get_personal_records(self) -> dict:
        """Per-exercise best, grouped case-insensitively ("bench press" ==
        "Bench Press" == "  BENCH PRESS ").

        Returns {canonical_key: {display, max_weight_kg, reps_at_max,
        max_weight_date, estimated_1rm_kg, one_rm_date}}. The estimated 1RM
        is the best Epley estimate across *every* logged set, not just the
        heaviest one: 95 kg x 10 (est. ~127 kg) beats 100 kg x 1.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT exercise, performed_at, sets_json FROM workouts"
            ).fetchall()
        prs: dict[str, dict] = {}
        for r in rows:
            display = " ".join(r["exercise"].split())
            key = display.lower()
            entry = prs.get(key)
            if entry is None:
                entry = prs[key] = {
                    "display": display,
                    "max_weight_kg": 0.0,
                    "reps_at_max": 0,
                    "max_weight_date": "",
                    "estimated_1rm_kg": 0.0,
                    "one_rm_date": "",
                }
            for s in json.loads(r["sets_json"]):
                try:
                    w = float(s.get("weight", 0))
                    reps = int(s.get("reps", 0))
                except (TypeError, ValueError, OverflowError):
                    continue
                if w <= 0 or reps <= 0:
                    continue
                epley = round(w * (1 + reps / 30.0), 1)
                if epley > entry["estimated_1rm_kg"]:
                    entry["estimated_1rm_kg"] = epley
                    entry["one_rm_date"] = r["performed_at"]
                if w > entry["max_weight_kg"]:
                    entry["max_weight_kg"] = w
                    entry["reps_at_max"] = reps
                    entry["max_weight_date"] = r["performed_at"]
        return prs

    # ---- nutrition -----------------------------------------------------

    def log_protein(self, grams: float, day: str | None = None) -> float:
        day = day or _today()
        with self._lock:
            self._conn.execute(
                "INSERT INTO protein_log (day, grams, logged_at) VALUES (?, ?, ?)",
                (day, float(grams), _now_iso()),
            )
            self._conn.commit()
            total = self._conn.execute(
                "SELECT COALESCE(SUM(grams), 0) FROM protein_log WHERE day = ?",
                (day,),
            ).fetchone()[0]
        return float(total)

    def get_protein_total(self, day: str | None = None) -> float:
        day = day or _today()
        with self._lock:
            total = self._conn.execute(
                "SELECT COALESCE(SUM(grams), 0) FROM protein_log WHERE day = ?",
                (day,),
            ).fetchone()[0]
        return float(total)
