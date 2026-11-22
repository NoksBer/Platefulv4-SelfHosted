#!/usr/bin/env python3
"""Plateful - self-hosted weekly meal planner. Python standard library only, fully offline."""
import datetime
import hashlib
import hmac
import html
import ipaddress
import json
import math
import mimetypes
import os
import random
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import seed
import seed_extra

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
DB_PATH = os.environ.get("PLATEFUL_DB", os.path.join(BASE, "data", "plateful.db"))
SLOTS = ["breakfast", "lunch", "dinner", "snack"]
SLOT_PRIORITY = {"dinner": 0, "lunch": 1, "breakfast": 2, "snack": 3}
SLOT_WEIGHT = {"dinner": 3.0, "lunch": 1.5, "breakfast": 1.0, "snack": 0.5}
DISCRETE = {"each", "can", "jar", "head", "bunch", "packet", "block"}
DIET_EXCLUDES = {
    "vegetarian": {"meat", "fish", "shellfish"},
    "vegan": {"meat", "fish", "shellfish", "dairy", "egg", "animal"},
    "gluten-free": {"gluten"},
    "dairy-free": {"dairy"},
}
ALLERGEN_FLAGS = ["dairy", "egg", "gluten", "nut", "soy", "fish", "shellfish"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT DEFAULT 'adult', portion REAL DEFAULT 1,
  diets TEXT DEFAULT '[]', allergies TEXT DEFAULT '[]', allergy_text TEXT DEFAULT '',
  likes TEXT DEFAULT '', dislikes TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, slots TEXT, minutes INTEGER DEFAULT 30,
  servings INTEGER DEFAULT 4, steps TEXT DEFAULT '', ingredients TEXT DEFAULT '[]', source TEXT DEFAULT 'custom');
CREATE TABLE IF NOT EXISTS pricebook (
  name TEXT PRIMARY KEY, unit TEXT, price REAL DEFAULT 0, aisle TEXT DEFAULT 'Other',
  flags TEXT DEFAULT '', on_hand INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS weeks (
  week_start TEXT PRIMARY KEY, budget REAL, grid TEXT, away TEXT DEFAULT '[]',
  guests REAL DEFAULT 0, quick INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS entries (
  week_start TEXT, day INTEGER, slot TEXT, recipe_id INTEGER, locked INTEGER DEFAULT 0, servings REAL,
  PRIMARY KEY (week_start, day, slot));
CREATE TABLE IF NOT EXISTS feedback (recipe_id INTEGER PRIMARY KEY, score INTEGER);
CREATE TABLE IF NOT EXISTS grocery_state (week_start TEXT, item TEXT, checked INTEGER, PRIMARY KEY (week_start, item));
CREATE TABLE IF NOT EXISTS extras (
  id INTEGER PRIMARY KEY AUTOINCREMENT, week_start TEXT, name TEXT, price REAL DEFAULT 0);
"""

# Accounts live in one central database; every household gets its own private database file,
# so one family can never see another family's data.
ACCOUNT_SCHEMA = """
CREATE TABLE IF NOT EXISTS gsettings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS households (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT DEFAULT '', created INTEGER);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, pw_hash TEXT, role TEXT DEFAULT 'member',
  superadmin INTEGER DEFAULT 0, household_id INTEGER, created INTEGER);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER, expires INTEGER);
CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT UNIQUE, created INTEGER, expires INTEGER, household_id INTEGER);
"""
MAX_HOUSEHOLDS = int(os.environ.get("PLATEFUL_MAX_HOUSEHOLDS", "100"))

# ----------------------------------------------------------------- auth helpers
CURRENT = threading.local()          # per-request: user, cookies, secure, ip
PBKDF2_ITERS = 200000
SESSION_SECONDS = 30 * 86400
COOKIE_NAME = "plateful_session"
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
FAILS = {}
FAIL_LOCK = threading.Lock()
TRUST_PROXY = os.environ.get("PLATEFUL_TRUST_PROXY") == "1"


def hash_password(pw, salt=None, iters=PBKDF2_ITERS):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
    return "pbkdf2$%d$%s$%s" % (iters, salt.hex(), h.hex())


def verify_password(pw, stored):
    try:
        _, iters, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, AttributeError):
        return False


DUMMY_HASH = hash_password("not-a-real-password")


def check_password_rules(pw):
    if not isinstance(pw, str) or len(pw) < 8:
        raise ApiError("Password must be at least 8 characters")
    if len(pw) > 200:
        raise ApiError("Password is too long")


def check_username(name):
    name = (name or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise ApiError("Username must be 3-32 characters: letters, numbers, . _ -")
    return name


def rate_check(ip):
    now = time.time()
    with FAIL_LOCK:
        lst = [t for t in FAILS.get(ip, []) if now - t < 600]
        FAILS[ip] = lst
        if len(lst) >= 8:
            raise ApiError("Too many failed attempts. Try again in a few minutes.", 429)


def rate_fail(ip):
    with FAIL_LOCK:
        FAILS.setdefault(ip, []).append(time.time())


def cookie_header(value, max_age):
    parts = ["%s=%s" % (COOKIE_NAME, value), "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=%d" % max_age]
    if getattr(CURRENT, "secure", False):
        parts.append("Secure")
    return "; ".join(parts)


def start_session(c, user_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    c.execute("DELETE FROM sessions WHERE expires<?", (now,))
    c.execute("INSERT INTO sessions(token_hash,user_id,expires) VALUES(?,?,?)",
              (hashlib.sha256(token.encode()).hexdigest(), user_id, now + SESSION_SECONDS))
    CURRENT.cookies.append(cookie_header(token, SESSION_SECONDS))


def user_public(u):
    return {"id": u["id"], "username": u["username"], "role": u["role"], "superadmin": bool(u["superadmin"])}


# ----------------------------------------------------------------- helpers
class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


DATA_DIR = os.path.dirname(DB_PATH)
ACCOUNTS_PATH = os.path.join(DATA_DIR, "accounts.db")


def open_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def acct_db():
    return open_db(ACCOUNTS_PATH)


def house_path(hid):
    # Household 1 keeps the original database file, so upgraded installs keep all their data.
    return DB_PATH if int(hid) == 1 else os.path.join(DATA_DIR, "household_%d.db" % int(hid))


def house_db(hid):
    path = house_path(hid)
    if not os.path.exists(path):
        init_house(path)
    return open_db(path)


def jl(s, default=None):
    try:
        return json.loads(s) if s else default
    except (ValueError, TypeError):
        return default


def csv_list(s):
    return [x.strip().lower() for x in (s or "").split(",") if x.strip()]


def default_grid():
    return {str(d): {s: s == "dinner" for s in SLOTS} for d in range(7)}


def clean_grid(g):
    out = {}
    for d in range(7):
        row = (g or {}).get(str(d), {})
        out[str(d)] = {s: bool(row.get(s)) for s in SLOTS}
    return out


def norm_week(ws):
    try:
        d = datetime.date.fromisoformat(ws)
    except (ValueError, TypeError):
        raise ApiError("bad week date")
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def shift_week(ws, days):
    return (datetime.date.fromisoformat(ws) + datetime.timedelta(days=days)).isoformat()


def init_accounts():
    a = acct_db()
    a.executescript(ACCOUNT_SCHEMA)
    now = int(time.time())
    if not a.execute("SELECT 1 FROM households LIMIT 1").fetchone():
        # Household 1 always exists; it owns the original database file.
        a.execute("INSERT INTO households(id,name,created) VALUES(1,'',?)", (now,))
        # Upgrade path: bring accounts from the old single-household database into the accounts DB.
        if os.path.exists(DB_PATH):
            old = open_db(DB_PATH)
            if old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                first_admin = None
                for r in old.execute("SELECT * FROM users ORDER BY id"):
                    sup = 1 if (r["role"] == "admin" and first_admin is None) else 0
                    if sup:
                        first_admin = r["id"]
                    a.execute("INSERT OR IGNORE INTO users(id,username,pw_hash,role,superadmin,household_id,created) "
                              "VALUES(?,?,?,?,?,1,?)", (r["id"], r["username"], r["pw_hash"], r["role"], sup, r["created"]))
                for r in old.execute("SELECT * FROM sessions WHERE expires>?", (now,)):
                    a.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?)", (r["token_hash"], r["user_id"], r["expires"]))
                for r in old.execute("SELECT * FROM invites WHERE expires>?", (now,)):
                    a.execute("INSERT OR IGNORE INTO invites(id,code_hash,created,expires,household_id) VALUES(?,?,?,?,1)",
                              (r["id"], r["code_hash"], r["created"], r["expires"]))
            old.close()
    a.commit()
    ids = [r["id"] for r in a.execute("SELECT id FROM households")]
    a.close()
    return ids


def init_house(path):
    c = open_db(path)
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM pricebook LIMIT 1").fetchone():
        for n, u, p, a, f in seed.PRICEBOOK:
            c.execute("INSERT INTO pricebook(name,unit,price,aisle,flags) VALUES(?,?,?,?,?)", (n, u, p, a, f))
    if not c.execute("SELECT value FROM settings WHERE key='seeded'").fetchone():
        for name, slots, mins, serv, ings, steps in seed.RECIPES:
            c.execute(
                "INSERT INTO recipes(name,slots,minutes,servings,steps,ingredients,source) VALUES(?,?,?,?,?,?,'starter')",
                (name, json.dumps(slots), mins, serv, steps, json.dumps([{"n": n, "q": q} for n, q in ings])))
        c.execute("INSERT OR REPLACE INTO settings VALUES('seeded','1')")
    row = c.execute("SELECT value FROM settings WHERE key='seed_version'").fetchone()
    if int(jl(row["value"], 1) if row else 1) < 2:
        for n, u, p, a, f in seed_extra.PRICEBOOK_V2:
            c.execute("INSERT OR IGNORE INTO pricebook(name,unit,price,aisle,flags) VALUES(?,?,?,?,?)", (n, u, p, a, f))
        for name, slots, mins, serv, ings, steps in seed_extra.RECIPES_V2:
            if not c.execute("SELECT 1 FROM recipes WHERE name=?", (name,)).fetchone():
                c.execute(
                    "INSERT INTO recipes(name,slots,minutes,servings,steps,ingredients,source) VALUES(?,?,?,?,?,?,'starter')",
                    (name, json.dumps(slots), mins, serv, steps, json.dumps([{"n": n, "q": q} for n, q in ings])))
        set_setting(c, "seed_version", 2)
    c.commit()
    c.close()


# ----------------------------------------------------------------- settings / people
SETTING_DEFAULTS = {"household_name": "Our Household", "store_name": "My Grocery Store",
                    "currency": "$", "setup_done": False}


def get_settings(c):
    s = dict(SETTING_DEFAULTS)
    for r in c.execute("SELECT key,value FROM settings"):
        if r["key"] in SETTING_DEFAULTS:
            s[r["key"]] = jl(r["value"], r["value"])
    return s


def set_setting(c, k, v):
    c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, json.dumps(v)))


def person_dict(r):
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "portion": r["portion"],
            "diets": jl(r["diets"], []), "allergies": jl(r["allergies"], []),
            "allergy_text": r["allergy_text"] or "", "likes": r["likes"] or "",
            "dislikes": r["dislikes"] or "", "active": bool(r["active"])}


def save_person(c, d, pid=None):
    name = (d.get("name") or "").strip()
    if not name:
        raise ApiError("Name is required")
    kind = "child" if d.get("kind") == "child" else "adult"
    try:
        portion = float(d.get("portion", 1 if kind == "adult" else 0.6))
    except (ValueError, TypeError):
        portion = 1.0
    vals = (name, kind, max(0.1, min(portion, 3)),
            json.dumps([x for x in d.get("diets", []) if x in DIET_EXCLUDES]),
            json.dumps([x for x in d.get("allergies", []) if x in ALLERGEN_FLAGS]),
            d.get("allergy_text", ""), d.get("likes", ""), d.get("dislikes", ""),
            1 if d.get("active", True) else 0)
    if pid:
        c.execute("UPDATE people SET name=?,kind=?,portion=?,diets=?,allergies=?,allergy_text=?,likes=?,dislikes=?,active=? WHERE id=?",
                  vals + (pid,))
        return pid
    cur = c.execute("INSERT INTO people(name,kind,portion,diets,allergies,allergy_text,likes,dislikes,active) VALUES(?,?,?,?,?,?,?,?,?)", vals)
    return cur.lastrowid


# ----------------------------------------------------------------- pricebook / recipes
def load_pricebook(c):
    return {r["name"]: {"name": r["name"], "unit": r["unit"], "price": r["price"], "aisle": r["aisle"],
                        "flags": r["flags"] or "", "on_hand": r["on_hand"]}
            for r in c.execute("SELECT * FROM pricebook ORDER BY aisle, name")}


def recipe_info(r, pb):
    ings = jl(r["ingredients"], [])
    flags = set()
    total = 0.0
    for i in ings:
        p = pb.get(i["n"])
        if p:
            flags |= set(p["flags"].split())
            total += i["q"] * p["price"]
    tags = []
    for diet, ex in DIET_EXCLUDES.items():
        if not (flags & ex):
            tags.append(diet)
    servings = max(1, r["servings"] or 4)
    return {"id": r["id"], "name": r["name"], "slots": jl(r["slots"], []), "minutes": r["minutes"],
            "servings": servings, "steps": r["steps"] or "", "ingredients": ings, "source": r["source"],
            "flags": sorted(flags), "tags": tags, "cost_total": round(total, 2),
            "cost_per_serving": round(total / servings, 2),
            "names": [i["n"] for i in ings]}


def load_recipes(c, pb):
    return [recipe_info(r, pb) for r in c.execute("SELECT * FROM recipes ORDER BY name")]


def ensure_pricebook(c, ings):
    for i in ings:
        if not c.execute("SELECT 1 FROM pricebook WHERE name=?", (i["n"],)).fetchone():
            c.execute("INSERT INTO pricebook(name,unit,price,aisle,flags) VALUES(?,?,0,'Other','')",
                      (i["n"], i.get("unit") or "each"))


def save_recipe(c, d, rid=None):
    name = (d.get("name") or "").strip()
    if not name:
        raise ApiError("Recipe name is required")
    slots = [s for s in d.get("slots", []) if s in SLOTS] or ["dinner"]
    ings = []
    for i in d.get("ingredients", []):
        n = (i.get("n") or "").strip().lower()
        try:
            q = float(i.get("q", 0))
        except (ValueError, TypeError):
            q = 0
        if n and q > 0:
            ings.append({"n": n, "q": q, "unit": i.get("unit", "")})
    if not ings:
        raise ApiError("Add at least one ingredient")
    ensure_pricebook(c, ings)
    ings = [{"n": i["n"], "q": i["q"]} for i in ings]
    vals = (name, json.dumps(slots), int(d.get("minutes") or 30), max(1, int(d.get("servings") or 4)),
            d.get("steps", ""), json.dumps(ings))
    if rid:
        c.execute("UPDATE recipes SET name=?,slots=?,minutes=?,servings=?,steps=?,ingredients=? WHERE id=?", vals + (rid,))
        return rid
    return c.execute("INSERT INTO recipes(name,slots,minutes,servings,steps,ingredients,source) VALUES(?,?,?,?,?,?,'custom')", vals).lastrowid


# ----------------------------------------------------------------- weeks
def get_week(c, ws):
    r = c.execute("SELECT * FROM weeks WHERE week_start=?", (ws,)).fetchone()
    if not r:
        prev = c.execute("SELECT * FROM weeks WHERE week_start<? ORDER BY week_start DESC LIMIT 1", (ws,)).fetchone()
        budget = prev["budget"] if prev else 150.0
        grid = prev["grid"] if prev else json.dumps(default_grid())
        quick = prev["quick"] if prev else 0
        c.execute("INSERT INTO weeks(week_start,budget,grid,away,guests,quick) VALUES(?,?,?,?,0,?)",
                  (ws, budget, grid, "[]", quick))
        c.commit()
        r = c.execute("SELECT * FROM weeks WHERE week_start=?", (ws,)).fetchone()
    return {"week_start": ws, "budget": r["budget"], "grid": clean_grid(jl(r["grid"], {})),
            "away": jl(r["away"], []), "guests": r["guests"] or 0, "quick": bool(r["quick"])}


def eating_people(c, cfg):
    return [person_dict(r) for r in c.execute("SELECT * FROM people WHERE active=1 ORDER BY id")
            if r["id"] not in cfg["away"]]


def headcount(people, cfg):
    return max(1.0, round(sum(p["portion"] for p in people) + cfg["guests"], 2))


def load_entries(c, ws):
    return [{"day": r["day"], "slot": r["slot"], "recipe_id": r["recipe_id"], "locked": bool(r["locked"]),
             "servings": r["servings"]}
            for r in c.execute("SELECT * FROM entries WHERE week_start=? ORDER BY day", (ws,))]


def build_items(entries, rmap, hc, pb):
    items = {}
    for e in entries:
        r = rmap.get(e["recipe_id"])
        if not r:
            continue
        mult = (e.get("servings") or hc) / r["servings"]
        for ing in r["ingredients"]:
            it = items.setdefault(ing["n"], {"qty": 0.0, "meals": []})
            it["qty"] += ing["q"] * mult
            if r["name"] not in it["meals"]:
                it["meals"].append(r["name"])
    out = []
    for n, it in items.items():
        p = pb.get(n, {"unit": "each", "price": 0, "aisle": "Other", "on_hand": 0})
        qty = it["qty"]
        qty = math.ceil(qty - 1e-9) if p["unit"] in DISCRETE else round(qty, 2)
        out.append({"name": n, "qty": qty, "unit": p["unit"], "aisle": p["aisle"], "price": p["price"],
                    "cost": round(qty * p["price"], 2), "on_hand": bool(p["on_hand"]), "meals": it["meals"]})
    return out


def items_total(items):
    return round(sum(i["cost"] for i in items if not i["on_hand"]), 2)


def extras_for(c, ws):
    return [{"id": r["id"], "name": r["name"], "price": r["price"]}
            for r in c.execute("SELECT * FROM extras WHERE week_start=?", (ws,))]


def week_payload(c, ws):
    pb = load_pricebook(c)
    recipes = load_recipes(c, pb)
    rmap = {r["id"]: r for r in recipes}
    cfg = get_week(c, ws)
    people = eating_people(c, cfg)
    hc = headcount(people, cfg)
    entries = load_entries(c, ws)
    fb = {r["recipe_id"]: r["score"] for r in c.execute("SELECT * FROM feedback")}
    out = []
    meal_sum = 0.0
    for e in entries:
        r = rmap.get(e["recipe_id"])
        if not r or not cfg["grid"][str(e["day"])].get(e["slot"]):
            continue
        sv = e["servings"] or hc
        cost = round(r["cost_total"] * sv / r["servings"], 2)
        meal_sum += cost
        out.append({"day": e["day"], "slot": e["slot"], "locked": e["locked"], "servings": e["servings"],
                    "recipe": {"id": r["id"], "name": r["name"], "minutes": r["minutes"], "tags": r["tags"],
                               "cost": cost, "feedback": fb.get(r["id"], 0)}})
    items = build_items(entries, rmap, hc, pb)
    extras = extras_for(c, ws)
    groceries = round(items_total(items) + sum(x["price"] for x in extras), 2)
    start = datetime.date.fromisoformat(ws)
    return {"week": cfg, "dates": [(start + datetime.timedelta(days=i)).isoformat() for i in range(7)],
            "headcount": hc, "entries": out,
            "totals": {"groceries": groceries, "meals": round(meal_sum, 2), "budget": cfg["budget"],
                       "remaining": round(cfg["budget"] - groceries, 2)}}


# ----------------------------------------------------------------- recommender
class Ctx:
    def __init__(self, c, ws):
        self.c, self.ws = c, ws
        self.pb = load_pricebook(c)
        self.recipes = load_recipes(c, self.pb)
        self.rmap = {r["id"]: r for r in self.recipes}
        self.cfg = get_week(c, ws)
        self.people = eating_people(c, self.cfg)
        self.hc = headcount(self.people, self.cfg)
        self.fb = {r["recipe_id"]: r["score"] for r in c.execute("SELECT * FROM feedback")}
        self.extras_total = sum(x["price"] for x in extras_for(c, ws))
        recent = c.execute("SELECT recipe_id FROM entries WHERE week_start>=? AND week_start<?",
                           (shift_week(ws, -14), ws)).fetchall()
        self.recent = {r["recipe_id"] for r in recent}
        self.excluded = set()
        self.allergy_words = []
        for p in self.people:
            for d in p["diets"]:
                self.excluded |= DIET_EXCLUDES.get(d, set())
            self.excluded |= set(p["allergies"])
            self.allergy_words += csv_list(p["allergy_text"])

    def eligible(self, r, slot=None):
        if slot and slot not in r["slots"]:
            return False
        if set(r["flags"]) & self.excluded:
            return False
        for w in self.allergy_words:
            if any(w in n for n in r["names"]) or w in r["name"].lower():
                return False
        return True

    def meal_cost(self, r, servings=None):
        return r["cost_total"] * (servings or self.hc) / r["servings"]

    def score(self, r, day, slot, chosen, target=None):
        s, why = 50.0, []
        hay = r["name"].lower() + " " + " ".join(r["names"])
        for p in self.people:
            hits = [w for w in csv_list(p["likes"]) if w in hay]
            if hits:
                s += 12 * len(hits)
                why.append("%s likes %s" % (p["name"], hits[0]))
            bad = [w for w in csv_list(p["dislikes"]) if w in hay]
            if bad:
                s -= 40 * len(bad)
                why.append("%s dislikes %s" % (p["name"], bad[0]))
        f = self.fb.get(r["id"], 0)
        if f > 0:
            s += 20
            why.append("household favorite")
        elif f < 0:
            s -= 60
            why.append("marked as not great")
        if r["id"] in self.recent:
            s -= 15
            why.append("had it recently")
        uses = sum(1 for x in chosen if x["recipe_id"] == r["id"])
        if uses:
            s -= 25 * uses
            why.append("already in this week")
        mine = set(r["names"])
        shared = set()
        for x in chosen:
            o = self.rmap.get(x["recipe_id"])
            if o and o["id"] != r["id"]:
                shared |= mine & set(o["names"])
        if shared:
            s += min(12, 3 * len(shared))
            why.append("shares %d ingredient%s with other meals" % (len(shared), "" if len(shared) == 1 else "s"))
        if self.cfg["quick"] and day <= 3 and slot == "dinner" and r["minutes"] <= 30:
            s += 15
            why.append("quick weeknight meal")
        if target:
            cost = self.meal_cost(r)
            if cost > target:
                s -= min(60, (cost / max(target, 0.01) - 1) * 25)
                why.append("pricier than your budget share")
            elif cost < target * 0.6:
                s += 3
                why.append("budget friendly")
        return s, why


def generate(c, ws, mode):
    ctx = Ctx(c, ws)
    entries = load_entries(c, ws)
    grid = ctx.cfg["grid"]
    entries = [e for e in entries if grid[str(e["day"])].get(e["slot"])]
    if mode == "all":
        entries = [e for e in entries if e["locked"]]
    have = {(e["day"], e["slot"]) for e in entries}
    targets = [(d, s) for d in range(7) for s in SLOTS if grid[str(d)][s] and (d, s) not in have]
    targets.sort(key=lambda t: (SLOT_PRIORITY[t[1]], t[0]))
    new = []

    def spent(es):
        return sum(ctx.meal_cost(ctx.rmap[e["recipe_id"]], e["servings"]) for e in es if e["recipe_id"] in ctx.rmap)

    for idx, (d, s) in enumerate(targets):
        remaining = ctx.cfg["budget"] * 0.92 - spent(entries + new)
        wsum = sum(SLOT_WEIGHT[t[1]] for t in targets[idx:])
        target = max(0.5, remaining) * SLOT_WEIGHT[s] / wsum
        cands = []
        for r in ctx.recipes:
            if ctx.eligible(r, s):
                sc, _ = ctx.score(r, d, s, entries + new, target)
                cands.append((sc, r))
        if not cands:
            continue
        cands.sort(key=lambda x: -x[0])
        top = cands[:5]
        weights = [max(1.0, sc - (top[0][0] - 25)) for sc, _ in top]
        pick = random.choices(top, weights=weights)[0][1]
        new.append({"day": d, "slot": s, "recipe_id": pick["id"], "locked": False, "servings": None})

    def total_of(es):
        return items_total(build_items(es, ctx.rmap, ctx.hc, ctx.pb)) + ctx.extras_total

    for _ in range(20):
        everything = entries + new
        tot = total_of(everything)
        if tot <= ctx.cfg["budget"] or not new:
            break
        best = None
        for e in new:
            cur = ctx.rmap[e["recipe_id"]]
            others = [x for x in everything if x is not e]
            cur_score = ctx.score(cur, e["day"], e["slot"], others)[0]
            pool = [r for r in ctx.recipes if r["id"] != cur["id"] and ctx.eligible(r, e["slot"])]
            for r in random.sample(pool, min(10, len(pool))):
                trial = [dict(x, recipe_id=r["id"]) if x is e else x for x in everything]
                red = tot - total_of(trial)
                if red < 0.25:
                    continue
                loss = max(0.0, cur_score - ctx.score(r, e["day"], e["slot"], others)[0])
                val = red / (1 + loss / 10)
                if not best or val > best[0]:
                    best = (val, e, r["id"])
        if not best:
            break
        best[1]["recipe_id"] = best[2]

    if mode == "all":
        c.execute("DELETE FROM entries WHERE week_start=? AND locked=0", (ws,))
    for e in new:
        c.execute("INSERT OR REPLACE INTO entries(week_start,day,slot,recipe_id,locked,servings) VALUES(?,?,?,?,0,NULL)",
                  (ws, e["day"], e["slot"], e["recipe_id"]))
    c.commit()


def suggest(c, ws, day, slot):
    ctx = Ctx(c, ws)
    chosen = [e for e in load_entries(c, ws) if not (e["day"] == day and e["slot"] == slot)]
    spent = sum(ctx.meal_cost(ctx.rmap[e["recipe_id"]], e["servings"]) for e in chosen if e["recipe_id"] in ctx.rmap)
    open_slots = sum(1 for d in range(7) for s in SLOTS if ctx.cfg["grid"][str(d)][s]) - len(chosen)
    target = max(0.5, ctx.cfg["budget"] * 0.92 - spent) * SLOT_WEIGHT[slot] / max(1.0, max(1, open_slots) * 1.2)
    out = []
    for r in ctx.recipes:
        if ctx.eligible(r, slot):
            sc, why = ctx.score(r, day, slot, chosen, target)
            out.append({"id": r["id"], "name": r["name"], "minutes": r["minutes"], "tags": r["tags"],
                        "cost": round(ctx.meal_cost(r), 2), "score": round(sc), "why": why[:3]})
    out.sort(key=lambda x: -x["score"])
    return out[:12]


# ----------------------------------------------------------------- routes
ROUTES = []


ACCOUNT_PREFIXES = ("/api/auth/", "/api/users", "/api/invites", "/api/site")


def route(method, pattern, auth="user"):
    """auth: 'public' (no login), 'user' (any signed-in account), 'admin' (household admin),
    'super' (site owner). Routes under ACCOUNT_PREFIXES get the central accounts DB; every other
    route gets the signed-in user's private household DB."""
    rx = re.compile("^" + pattern + "$")
    kind = "acct" if pattern.startswith(ACCOUNT_PREFIXES) else "house"

    def deco(fn):
        ROUTES.append((method, rx, fn, auth, kind))
        return fn
    return deco


def gsetting(c, key, default=None):
    r = c.execute("SELECT value FROM gsettings WHERE key=?", (key,)).fetchone()
    return jl(r["value"], default) if r else default


WS = r"/api/week/(\d{4}-\d{2}-\d{2})"


# ---- accounts
@route("GET", "/api/auth/status", auth="public")
def r_auth_status(c, m, q, b):
    n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    u = CURRENT.user
    return {"has_users": n > 0, "user": user_public(u) if u else None,
            "signup_open": bool(gsetting(c, "signup_open", False))}


@route("POST", "/api/auth/setup", auth="public")
def r_auth_setup(c, m, q, b):
    username = check_username(b.get("username"))
    check_password_rules(b.get("password"))
    c.execute("BEGIN IMMEDIATE")
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        c.rollback()
        raise ApiError("Setup is already complete", 403)
    uid = c.execute("INSERT INTO users(username,pw_hash,role,superadmin,household_id,created) VALUES(?,?,'admin',1,1,?)",
                    (username, hash_password(b["password"]), int(time.time()))).lastrowid
    start_session(c, uid)
    c.commit()
    return {"user": {"id": uid, "username": username, "role": "admin", "superadmin": True}}


@route("POST", "/api/auth/signup", auth="public")
def r_auth_signup(c, m, q, b):
    """Open signup (when the site owner enables it): creates a brand-new, private household."""
    ip = CURRENT.ip
    rate_check(ip)
    if not gsetting(c, "signup_open", False):
        raise ApiError("New family signup is turned off. Ask for an invite code instead.", 403)
    username = check_username(b.get("username"))
    check_password_rules(b.get("password"))
    c.execute("BEGIN IMMEDIATE")
    if c.execute("SELECT COUNT(*) FROM households").fetchone()[0] >= MAX_HOUSEHOLDS:
        c.rollback()
        raise ApiError("This server has reached its family limit", 403)
    if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        c.rollback()
        raise ApiError("That username is taken")
    hid = c.execute("INSERT INTO households(name,created) VALUES('',?)", (int(time.time()),)).lastrowid
    uid = c.execute("INSERT INTO users(username,pw_hash,role,superadmin,household_id,created) VALUES(?,?,'admin',0,?,?)",
                    (username, hash_password(b["password"]), hid, int(time.time()))).lastrowid
    init_house(house_path(hid))
    start_session(c, uid)
    c.commit()
    rate_fail(ip)   # counts toward the per-IP limit so one address can't mass-create families
    return {"user": {"id": uid, "username": username, "role": "admin", "superadmin": False}}


@route("POST", "/api/auth/login", auth="public")
def r_auth_login(c, m, q, b):
    ip = CURRENT.ip
    rate_check(ip)
    username = (b.get("username") or "").strip().lower()
    pw = b.get("password") or ""
    u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    ok = verify_password(pw if isinstance(pw, str) else "", u["pw_hash"] if u else DUMMY_HASH)
    if not (u and ok):
        rate_fail(ip)
        time.sleep(0.4)
        raise ApiError("Wrong username or password", 401)
    with FAIL_LOCK:
        FAILS.pop(ip, None)
    start_session(c, u["id"])
    c.commit()
    return {"user": user_public(u)}


INVITE_SECONDS = 7 * 86400


@route("POST", "/api/auth/register", auth="public")
def r_auth_register(c, m, q, b):
    ip = CURRENT.ip
    rate_check(ip)
    code = (b.get("code") or "").strip()
    username = check_username(b.get("username"))
    check_password_rules(b.get("password"))
    c.execute("BEGIN IMMEDIATE")
    inv = c.execute("SELECT id, household_id FROM invites WHERE code_hash=? AND expires>?",
                    (hashlib.sha256(code.encode()).hexdigest(), int(time.time()))).fetchone() if code else None
    if not inv:
        c.rollback()
        rate_fail(ip)
        raise ApiError("That invite code is invalid or has expired", 403)
    if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        c.rollback()
        raise ApiError("That username is taken")
    uid = c.execute("INSERT INTO users(username,pw_hash,role,superadmin,household_id,created) VALUES(?,?,'member',0,?,?)",
                    (username, hash_password(b["password"]), inv["household_id"], int(time.time()))).lastrowid
    c.execute("DELETE FROM invites WHERE id=?", (inv["id"],))
    start_session(c, uid)
    c.commit()
    return {"user": {"id": uid, "username": username, "role": "member", "superadmin": False}}


@route("GET", "/api/invites", auth="admin")
def r_invites(c, m, q, b):
    now = int(time.time())
    c.execute("DELETE FROM invites WHERE expires<?", (now,))
    c.commit()
    return [{"id": r["id"], "expires": r["expires"]}
            for r in c.execute("SELECT * FROM invites WHERE household_id=? ORDER BY id", (CURRENT.user["household_id"],))]


@route("POST", "/api/invites", auth="admin")
def r_invite_add(c, m, q, b):
    code = secrets.token_urlsafe(8)
    now = int(time.time())
    c.execute("INSERT INTO invites(code_hash,created,expires,household_id) VALUES(?,?,?,?)",
              (hashlib.sha256(code.encode()).hexdigest(), now, now + INVITE_SECONDS, CURRENT.user["household_id"]))
    c.commit()
    return {"code": code, "expires": now + INVITE_SECONDS}


@route("DELETE", r"/api/invites/(\d+)", auth="admin")
def r_invite_del(c, m, q, b):
    c.execute("DELETE FROM invites WHERE id=? AND household_id=?", (int(m.group(1)), CURRENT.user["household_id"]))
    c.commit()
    return {"ok": True}


@route("POST", "/api/auth/logout")
def r_auth_logout(c, m, q, b):
    c.execute("DELETE FROM sessions WHERE token_hash=?", (CURRENT.user["token_hash"],))
    c.commit()
    CURRENT.cookies.append(cookie_header("", 0))
    return {"ok": True}


@route("POST", "/api/auth/password")
def r_auth_password(c, m, q, b):
    u = c.execute("SELECT * FROM users WHERE id=?", (CURRENT.user["id"],)).fetchone()
    if not verify_password(b.get("current") or "", u["pw_hash"]):
        rate_fail(CURRENT.ip)
        raise ApiError("Current password is wrong", 403)
    check_password_rules(b.get("new"))
    c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(b["new"]), u["id"]))
    c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    start_session(c, u["id"])
    c.commit()
    return {"ok": True}


def admin_count(c, hid):
    return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND household_id=?", (hid,)).fetchone()[0]


def household_user(c, uid):
    """A user in the caller's own household, or 404 - admins can never touch other families."""
    u = c.execute("SELECT * FROM users WHERE id=? AND household_id=?", (uid, CURRENT.user["household_id"])).fetchone()
    if not u:
        raise ApiError("No such user", 404)
    return u


@route("GET", "/api/users", auth="admin")
def r_users(c, m, q, b):
    return [{"id": r["id"], "username": r["username"], "role": r["role"]}
            for r in c.execute("SELECT * FROM users WHERE household_id=? ORDER BY id", (CURRENT.user["household_id"],))]


@route("POST", "/api/users", auth="admin")
def r_user_add(c, m, q, b):
    username = check_username(b.get("username"))
    check_password_rules(b.get("password"))
    role = "admin" if b.get("role") == "admin" else "member"
    if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise ApiError("That username is taken")
    c.execute("INSERT INTO users(username,pw_hash,role,superadmin,household_id,created) VALUES(?,?,?,0,?,?)",
              (username, hash_password(b["password"]), role, CURRENT.user["household_id"], int(time.time())))
    c.commit()
    return {"ok": True}


@route("PUT", r"/api/users/(\d+)", auth="admin")
def r_user_put(c, m, q, b):
    uid = int(m.group(1))
    u = household_user(c, uid)
    if b.get("password"):
        check_password_rules(b["password"])
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(b["password"]), uid))
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    if b.get("role") in ("admin", "member") and b["role"] != u["role"]:
        if u["role"] == "admin" and admin_count(c, u["household_id"]) <= 1:
            raise ApiError("There must be at least one admin")
        c.execute("UPDATE users SET role=? WHERE id=?", (b["role"], uid))
    c.commit()
    return {"ok": True}


@route("DELETE", r"/api/users/(\d+)", auth="admin")
def r_user_del(c, m, q, b):
    uid = int(m.group(1))
    u = household_user(c, uid)
    if uid == CURRENT.user["id"]:
        raise ApiError("You can't remove your own account")
    if u["superadmin"]:
        raise ApiError("The site owner account can't be removed")
    if u["role"] == "admin" and admin_count(c, u["household_id"]) <= 1:
        raise ApiError("There must be at least one admin")
    c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    c.execute("DELETE FROM users WHERE id=?", (uid,))
    c.commit()
    return {"ok": True}


# ---- site owner: control who can start new families
@route("GET", "/api/site", auth="super")
def r_site(c, m, q, b):
    hs = []
    for h in c.execute("SELECT * FROM households ORDER BY id"):
        members = [r["username"] for r in c.execute("SELECT username FROM users WHERE household_id=? ORDER BY id", (h["id"],))]
        hs.append({"id": h["id"], "members": members, "own": h["id"] == CURRENT.user["household_id"]})
    return {"signup_open": bool(gsetting(c, "signup_open", False)), "households": hs, "max": MAX_HOUSEHOLDS}


@route("PUT", "/api/site", auth="super")
def r_site_put(c, m, q, b):
    c.execute("INSERT OR REPLACE INTO gsettings VALUES('signup_open',?)", (json.dumps(bool(b.get("signup_open"))),))
    c.commit()
    return {"ok": True}


@route("DELETE", r"/api/site/households/(\d+)", auth="super")
def r_site_household_del(c, m, q, b):
    hid = int(m.group(1))
    if hid == 1 or hid == CURRENT.user["household_id"]:
        raise ApiError("You can't delete your own household")
    if not c.execute("SELECT 1 FROM households WHERE id=?", (hid,)).fetchone():
        raise ApiError("No such household", 404)
    c.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE household_id=?)", (hid,))
    c.execute("DELETE FROM users WHERE household_id=?", (hid,))
    c.execute("DELETE FROM invites WHERE household_id=?", (hid,))
    c.execute("DELETE FROM households WHERE id=?", (hid,))
    c.commit()
    base = house_path(hid)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(base + suffix)
        except OSError:
            pass
    return {"ok": True}


# ---- recipe import from a web page (schema.org JSON-LD)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def check_public_host(host, port):
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ApiError("Could not find that website")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ApiError("That address isn't allowed")


def fetch_page(url):
    for _ in range(4):
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ApiError("Enter a full web address starting with http:// or https://")
        check_public_host(p.hostname, p.port or (443 if p.scheme == "https" else 80))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Plateful recipe import)", "Accept": "text/html"})
        try:
            with OPENER.open(req, timeout=10) as resp:
                raw = resp.read(2000001)
                if len(raw) > 2000000:
                    raise ApiError("That page is too large")
                return raw.decode(resp.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                url = urljoin(url, e.headers["Location"])
                continue
            raise ApiError("The site answered with an error (HTTP %d); it may block automated access" % e.code)
        except (urllib.error.URLError, socket.timeout, OSError):
            raise ApiError("Could not reach that website")
    raise ApiError("Too many redirects")


LD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def find_recipe(node):
    if isinstance(node, list):
        for x in node:
            r = find_recipe(x)
            if r:
                return r
    elif isinstance(node, dict):
        t = node.get("@type")
        if "Recipe" in (t if isinstance(t, list) else [t]):
            return node
        for key in ("@graph", "mainEntity"):
            if key in node:
                r = find_recipe(node[key])
                if r:
                    return r
    return None


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s)))).strip()


def flatten_steps(node):
    out = []
    if isinstance(node, str):
        out.append(strip_tags(node))
    elif isinstance(node, list):
        for x in node:
            out += flatten_steps(x)
    elif isinstance(node, dict):
        if node.get("itemListElement"):
            out += flatten_steps(node["itemListElement"])
        elif node.get("text"):
            out.append(strip_tags(node["text"]))
    return [s for s in out if s]


FRACS = {"½": .5, "¼": .25, "¾": .75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": .125}
UNIT_ALIASES = {"teaspoon": "tsp", "teaspoons": "tsp", "tsp": "tsp", "tablespoon": "tbsp", "tablespoons": "tbsp",
                "tbsp": "tbsp", "tbs": "tbsp", "cup": "cup", "cups": "cup", "ounce": "oz", "ounces": "oz", "oz": "oz",
                "pound": "lb", "pounds": "lb", "lb": "lb", "lbs": "lb", "gram": "g", "grams": "g", "g": "g",
                "kg": "kg", "can": "can", "cans": "can", "jar": "jar", "clove": "clove", "cloves": "clove",
                "slice": "slice", "slices": "slice", "bunch": "bunch", "head": "head", "packet": "packet",
                "package": "packet"}
CONV = {"tsp": ("vol", 1), "tbsp": ("vol", 3), "cup": ("vol", 48), "oz": ("wt", 1), "lb": ("wt", 16),
        "g": ("wt", 0.03527), "kg": ("wt", 35.27)}
SKIP_WORDS = ("salt", "pepper", "water", "to taste")


def parse_ing_line(line, pb):
    s = re.sub(r"\(.*?\)", "", html.unescape(line).lower()).strip()
    for k, v in FRACS.items():
        s = s.replace(k, " %s " % v)
    qty, rest = 0.0, s
    while True:
        m = re.match(r"\s*(\d+(?:\.\d+)?)(?:\s*/\s*(\d+))?", rest)
        if not m:
            break
        qty += float(m.group(1)) / (float(m.group(2)) if m.group(2) else 1)
        rest = rest[m.end():]
        rm = re.match(r"\s*(?:-|to)\s*\d+(?:\.\d+)?(?:\s*/\s*\d+)?", rest)
        if rm:
            rest = rest[rm.end():]
    rest = rest.strip()
    unit = ""
    um = re.match(r"([a-z]+)\.?\s+(.*)", rest)
    if um and um.group(1) in UNIT_ALIASES:
        unit, rest = UNIT_ALIASES[um.group(1)], um.group(2)
    name_text = re.split(r",| - ", rest)[0].strip()
    if any(w in name_text for w in SKIP_WORDS) and qty == 0:
        return {"n": name_text, "q": 0, "unit": "", "raw": line, "matched": True}
    best = None
    for n in pb:
        if (n in rest or n.rstrip("s") in rest) and (best is None or len(n) > len(best)):
            best = n
    if best:
        pu = pb[best]["unit"]
        if unit in CONV and pu in CONV and CONV[unit][0] == CONV[pu][0]:
            qty = qty * CONV[unit][1] / CONV[pu][1]
        elif unit == "clove" and pu == "head":
            qty = qty / 10
        return {"n": best, "q": round(qty, 2), "unit": pu, "raw": line, "matched": True}
    return {"n": name_text, "q": round(qty, 2) or 1, "unit": unit or "each", "raw": line, "matched": False}


@route("POST", "/api/recipes/import-url")
def r_recipe_import(c, m, q, b):
    url = (b.get("url") or "").strip()
    page = fetch_page(url)
    recipe = None
    for block in LD_RE.findall(page):
        try:
            recipe = find_recipe(json.loads(html.unescape(block.strip())))
        except ValueError:
            continue
        if recipe:
            break
    if not recipe:
        raise ApiError("No recipe data found on that page. Many sites only publish it for search engines - "
                       "try a different recipe page, or add the recipe by hand.")
    pb = load_pricebook(c)
    yld = recipe.get("recipeYield")
    ym = re.search(r"\d+", " ".join(map(str, yld)) if isinstance(yld, list) else str(yld or ""))
    tm = re.match(r"PT?(?:(\d+)H)?(?:(\d+)M)?", str(recipe.get("totalTime") or recipe.get("cookTime") or ""))
    mins = (int(tm.group(1) or 0) * 60 + int(tm.group(2) or 0)) if tm else 0
    steps = "\n".join(flatten_steps(recipe.get("recipeInstructions")))
    steps += "\n\nSource: " + url
    return {"name": strip_tags(recipe.get("name") or "Imported recipe"), "minutes": mins or 30,
            "servings": int(ym.group()) if ym and int(ym.group()) > 0 else 4, "steps": steps,
            "ingredients": [parse_ing_line(str(x), pb) for x in (recipe.get("recipeIngredient") or [])]}


@route("GET", "/api/bootstrap")
def r_boot(c, m, q, b):
    today = datetime.date.today()
    return {"settings": get_settings(c),
            "people": [person_dict(r) for r in c.execute("SELECT * FROM people ORDER BY id")],
            "this_week": (today - datetime.timedelta(days=today.weekday())).isoformat(),
            "diets": list(DIET_EXCLUDES), "allergens": ALLERGEN_FLAGS, "slots": SLOTS}


@route("PUT", "/api/settings", auth="admin")
def r_settings(c, m, q, b):
    for k in ("household_name", "store_name", "currency"):
        if k in b:
            set_setting(c, k, str(b[k])[:60])
    c.commit()
    return get_settings(c)


@route("POST", "/api/setup", auth="admin")
def r_setup(c, m, q, b):
    set_setting(c, "household_name", str(b.get("household_name") or "Our Household")[:60])
    set_setting(c, "store_name", str(b.get("store_name") or "My Grocery Store")[:60])
    set_setting(c, "currency", str(b.get("currency") or "$")[:4])
    for p in b.get("people", []):
        if (p.get("name") or "").strip():
            save_person(c, p)
    ws = norm_week(b.get("week") or datetime.date.today().isoformat())
    get_week(c, ws)
    c.execute("UPDATE weeks SET budget=?, grid=? WHERE week_start=?",
              (float(b.get("budget") or 150), json.dumps(clean_grid(b.get("grid"))), ws))
    set_setting(c, "setup_done", True)
    c.commit()
    return {"ok": True}


@route("POST", "/api/people")
def r_people_add(c, m, q, b):
    pid = save_person(c, b)
    c.commit()
    return {"id": pid}


@route("PUT", r"/api/people/(\d+)")
def r_people_put(c, m, q, b):
    save_person(c, b, int(m.group(1)))
    c.commit()
    return {"ok": True}


@route("DELETE", r"/api/people/(\d+)")
def r_people_del(c, m, q, b):
    c.execute("DELETE FROM people WHERE id=?", (int(m.group(1)),))
    c.commit()
    return {"ok": True}


@route("GET", "/api/recipes")
def r_recipes(c, m, q, b):
    return load_recipes(c, load_pricebook(c))


@route("POST", "/api/recipes")
def r_recipe_add(c, m, q, b):
    rid = save_recipe(c, b)
    c.commit()
    return {"id": rid}


@route("PUT", r"/api/recipes/(\d+)")
def r_recipe_put(c, m, q, b):
    save_recipe(c, b, int(m.group(1)))
    c.commit()
    return {"ok": True}


@route("DELETE", r"/api/recipes/(\d+)")
def r_recipe_del(c, m, q, b):
    rid = int(m.group(1))
    c.execute("DELETE FROM recipes WHERE id=?", (rid,))
    c.execute("DELETE FROM entries WHERE recipe_id=?", (rid,))
    c.execute("DELETE FROM feedback WHERE recipe_id=?", (rid,))
    c.commit()
    return {"ok": True}


@route("POST", r"/api/recipes/(\d+)/feedback")
def r_feedback(c, m, q, b):
    score = int(b.get("score", 0))
    rid = int(m.group(1))
    if score == 0:
        c.execute("DELETE FROM feedback WHERE recipe_id=?", (rid,))
    else:
        c.execute("INSERT OR REPLACE INTO feedback VALUES(?,?)", (rid, 1 if score > 0 else -1))
    c.commit()
    return {"ok": True}


@route("GET", "/api/pricebook")
def r_pb(c, m, q, b):
    return list(load_pricebook(c).values())


@route("POST", "/api/pricebook")
def r_pb_save(c, m, q, b):
    name = (b.get("name") or "").strip().lower()
    if not name:
        raise ApiError("Name is required")
    flags = " ".join(f for f in (b.get("flags") or "").split()
                     if f in ALLERGEN_FLAGS + ["meat", "animal"])
    c.execute("INSERT OR REPLACE INTO pricebook(name,unit,price,aisle,flags,on_hand) VALUES(?,?,?,?,?,?)",
              (name, (b.get("unit") or "each").strip(), float(b.get("price") or 0),
               (b.get("aisle") or "Other").strip(), flags, 1 if b.get("on_hand") else 0))
    c.commit()
    return {"ok": True}


@route("DELETE", "/api/pricebook")
def r_pb_del(c, m, q, b):
    name = (q.get("name") or [""])[0]
    for r in c.execute("SELECT name, ingredients FROM recipes"):
        if any(i["n"] == name for i in jl(r["ingredients"], [])):
            raise ApiError("Used by recipe '%s' - remove it there first" % r["name"])
    c.execute("DELETE FROM pricebook WHERE name=?", (name,))
    c.commit()
    return {"ok": True}


@route("POST", "/api/pricebook/import")
def r_pb_import(c, m, q, b):
    n = 0
    for line in (b.get("csv") or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or parts[0].lower() == "name":
            continue
        try:
            price = float(parts[2].lstrip("$"))
        except ValueError:
            continue
        name = parts[0].lower()
        if c.execute("SELECT 1 FROM pricebook WHERE name=?", (name,)).fetchone():
            c.execute("UPDATE pricebook SET unit=?, price=? WHERE name=?", (parts[1], price, name))
        else:
            c.execute("INSERT INTO pricebook(name,unit,price,aisle) VALUES(?,?,?,?)",
                      (name, parts[1], price, parts[3] if len(parts) > 3 else "Other"))
        n += 1
    c.commit()
    return {"imported": n}


@route("GET", WS)
def r_week(c, m, q, b):
    return week_payload(c, norm_week(m.group(1)))


@route("PUT", WS)
def r_week_put(c, m, q, b):
    ws = norm_week(m.group(1))
    cfg = get_week(c, ws)
    budget = float(b.get("budget", cfg["budget"]))
    grid = clean_grid(b["grid"]) if "grid" in b else cfg["grid"]
    away = [int(x) for x in b.get("away", cfg["away"])]
    c.execute("UPDATE weeks SET budget=?, grid=?, away=?, guests=?, quick=? WHERE week_start=?",
              (max(0.0, budget), json.dumps(grid), json.dumps(away),
               max(0.0, float(b.get("guests", cfg["guests"]))), 1 if b.get("quick", cfg["quick"]) else 0, ws))
    for d in range(7):
        for s in SLOTS:
            if not grid[str(d)][s]:
                c.execute("DELETE FROM entries WHERE week_start=? AND day=? AND slot=?", (ws, d, s))
    c.commit()
    return week_payload(c, ws)


@route("POST", WS + "/generate")
def r_generate(c, m, q, b):
    ws = norm_week(m.group(1))
    generate(c, ws, "all" if b.get("mode") == "all" else "fill")
    return week_payload(c, ws)


@route("POST", WS + "/clear")
def r_clear(c, m, q, b):
    ws = norm_week(m.group(1))
    c.execute("DELETE FROM entries WHERE week_start=? AND locked=0", (ws,))
    c.commit()
    return week_payload(c, ws)


@route("POST", WS + "/copy")
def r_copy(c, m, q, b):
    ws = norm_week(m.group(1))
    src = norm_week(b.get("from") or shift_week(ws, -7))
    s = get_week(c, src)
    get_week(c, ws)
    c.execute("UPDATE weeks SET budget=?, grid=?, quick=? WHERE week_start=?",
              (s["budget"], json.dumps(s["grid"]), 1 if s["quick"] else 0, ws))
    c.execute("DELETE FROM entries WHERE week_start=?", (ws,))
    c.execute("INSERT INTO entries SELECT ?, day, slot, recipe_id, locked, servings FROM entries WHERE week_start=?",
              (ws, src))
    c.commit()
    return week_payload(c, ws)


@route("POST", WS + "/entry")
def r_entry(c, m, q, b):
    ws = norm_week(m.group(1))
    day, slot = int(b["day"]), b["slot"]
    if slot not in SLOTS or not 0 <= day <= 6:
        raise ApiError("bad slot")
    if b.get("remove"):
        c.execute("DELETE FROM entries WHERE week_start=? AND day=? AND slot=?", (ws, day, slot))
    else:
        old = c.execute("SELECT * FROM entries WHERE week_start=? AND day=? AND slot=?", (ws, day, slot)).fetchone()
        rid = b.get("recipe_id", old["recipe_id"] if old else None)
        if rid is None:
            raise ApiError("recipe_id required")
        locked = b.get("locked", bool(old["locked"]) if old else False)
        serv = b.get("servings", old["servings"] if old else None)
        c.execute("INSERT OR REPLACE INTO entries(week_start,day,slot,recipe_id,locked,servings) VALUES(?,?,?,?,?,?)",
                  (ws, day, slot, int(rid), 1 if locked else 0, serv))
    c.commit()
    return week_payload(c, ws)


@route("GET", WS + "/suggest")
def r_suggest(c, m, q, b):
    ws = norm_week(m.group(1))
    return suggest(c, ws, int(q["day"][0]), q["slot"][0])


@route("GET", WS + "/grocery")
def r_grocery(c, m, q, b):
    ws = norm_week(m.group(1))
    pb = load_pricebook(c)
    recipes = load_recipes(c, pb)
    rmap = {r["id"]: r for r in recipes}
    cfg = get_week(c, ws)
    hc = headcount(eating_people(c, cfg), cfg)
    entries = [e for e in load_entries(c, ws) if cfg["grid"][str(e["day"])].get(e["slot"])]
    items = build_items(entries, rmap, hc, pb)
    checked = {r["item"] for r in c.execute("SELECT item FROM grocery_state WHERE week_start=? AND checked=1", (ws,))}
    aisles = {}
    pantry = []
    for it in sorted(items, key=lambda i: i["name"]):
        it["checked"] = it["name"] in checked
        if it["on_hand"]:
            pantry.append(it)
        else:
            aisles.setdefault(it["aisle"], []).append(it)
    extras = extras_for(c, ws)
    total = round(items_total(items) + sum(x["price"] for x in extras), 2)
    return {"aisles": [{"name": k, "items": v} for k, v in sorted(aisles.items())], "pantry": pantry,
            "extras": extras, "total": total, "budget": cfg["budget"], "meal_count": len(entries)}


@route("POST", WS + "/grocery/check")
def r_check(c, m, q, b):
    c.execute("INSERT OR REPLACE INTO grocery_state VALUES(?,?,?)",
              (norm_week(m.group(1)), b["name"], 1 if b.get("checked") else 0))
    c.commit()
    return {"ok": True}


@route("POST", WS + "/extras")
def r_extra_add(c, m, q, b):
    name = (b.get("name") or "").strip()
    if not name:
        raise ApiError("Name is required")
    c.execute("INSERT INTO extras(week_start,name,price) VALUES(?,?,?)",
              (norm_week(m.group(1)), name, float(b.get("price") or 0)))
    c.commit()
    return {"ok": True}


@route("DELETE", WS + r"/extras/(\d+)")
def r_extra_del(c, m, q, b):
    c.execute("DELETE FROM extras WHERE id=?", (int(m.group(2)),))
    c.commit()
    return {"ok": True}


@route("GET", "/api/export", auth="admin")
def r_export(c, m, q, b):
    out = {}
    for t in ("settings", "people", "recipes", "pricebook", "weeks", "entries", "feedback", "extras"):
        out[t] = [dict(r) for r in c.execute("SELECT * FROM " + t)]
    return out


# ----------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "Plateful/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("PLATEFUL_QUIET") != "1":
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if getattr(CURRENT, "secure", False):
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for ck in getattr(CURRENT, "cookies", []):
            self.send_header("Set-Cookie", ck)

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
        self.end_headers()
        self.wfile.write(data)

    def lookup_user(self, c):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            tok = SimpleCookie(raw).get(COOKIE_NAME)
        except Exception:
            return None
        if not tok or not tok.value:
            return None
        th = hashlib.sha256(tok.value.encode()).hexdigest()
        r = c.execute("SELECT u.id, u.username, u.role, u.superadmin, u.household_id, s.token_hash "
                      "FROM sessions s JOIN users u ON u.id=s.user_id "
                      "WHERE s.token_hash=? AND s.expires>?", (th, int(time.time()))).fetchone()
        return dict(r) if r else None

    def handle_any(self, method):
        u = urlparse(self.path)
        path = unquote(u.path)
        CURRENT.user, CURRENT.cookies = None, []
        CURRENT.secure = (self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                          or os.environ.get("PLATEFUL_SECURE_COOKIES") == "1")
        CURRENT.ip = self.client_address[0]
        if TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            CURRENT.ip = fwd or CURRENT.ip
        if path.startswith("/api/"):
            if method != "GET" and self.headers.get("X-Plateful") != "1":
                return self.send_json({"error": "forbidden"}, 403)
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1000000:
                return self.send_json({"error": "request too large"}, 413)
            if n:
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self.send_json({"error": "invalid JSON"}, 400)
                if not isinstance(body, dict):
                    return self.send_json({"error": "invalid JSON"}, 400)
            q = parse_qs(u.query)
            for meth, rx, fn, auth, kind in ROUTES:
                m = rx.match(path)
                if meth == method and m:
                    a = acct_db()
                    c = a
                    try:
                        CURRENT.user = self.lookup_user(a)
                        if auth != "public" and not CURRENT.user:
                            return self.send_json({"error": "Please sign in"}, 401)
                        if auth == "admin" and CURRENT.user["role"] != "admin":
                            return self.send_json({"error": "Admins only"}, 403)
                        if auth == "super" and not CURRENT.user["superadmin"]:
                            return self.send_json({"error": "Site owner only"}, 403)
                        if kind == "house":
                            c = house_db(CURRENT.user["household_id"])
                        return self.send_json(fn(c, m, q, body))
                    except ApiError as e:
                        return self.send_json({"error": str(e)}, e.status)
                    except (KeyError, ValueError, TypeError) as e:
                        return self.send_json({"error": "bad request: %s" % e}, 400)
                    except Exception:
                        traceback.print_exc()
                        return self.send_json({"error": "server error"}, 500)
                    finally:
                        if c is not a:
                            c.close()
                        a.close()
            return self.send_json({"error": "not found"}, 404)
        if method != "GET":
            return self.send_json({"error": "not found"}, 404)
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.security_headers()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")

    def do_DELETE(self):
        self.handle_any("DELETE")


def main():
    for hid in init_accounts():
        init_house(house_path(hid))
    host = os.environ.get("PLATEFUL_HOST", "0.0.0.0")
    port = int(os.environ.get("PLATEFUL_PORT", "8080"))
    print("Plateful running on http://%s:%d  (db: %s)" % (host, port, DB_PATH))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
