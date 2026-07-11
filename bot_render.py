# KBJU REWORK VERSION
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, KeyboardButton, ReplyKeyboardMarkup
from datetime import datetime, timedelta
from html import escape
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import quote
from aiohttp import web
import threading
import asyncio

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from service.catalog import (
    show_catalog,
    show_product,
    get_products,
    build_caption,
    build_keyboard,
)


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🍽 Меню", "🛍 Корзина"],
            ["📦 Мои заказы"],
            ["📊 Рассчитать КБЖУ"],
            ["💬 Связаться с менеджером"]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


TOKEN = "8447362025:AAFJQGBdXpP2A4cKEZYcDz-fBHy8c9LEEVE"

carts = {}

order_data = {}

kbju_data = {}

orders = {}

ORDER_CHAT_ID = int(os.getenv("ORDER_CHAT_ID", "-5442251534"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://eatfit-bot.onrender.com").rstrip("/")
APP_VERSION = "owner-admin-v1"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

telegram_app = None

DEFAULT_DATA_DIR = "/var/data" if Path("/var/data").exists() else "."
DATA_DIR = Path(os.getenv("EATFIT_DATA_DIR") or os.getenv("RENDER_DATA_DIR") or DEFAULT_DATA_DIR)
ORDERS_FILE = DATA_DIR / "orders.json"
USERS_FILE = DATA_DIR / "users.json"
ADMIN_TOKENS_FILE = DATA_DIR / "admin_tokens.json"
WELCOME_BONUS = 30000
WELCOME_BONUS_DAYS = 7
ORDER_BONUS_DAYS = 30
XP_PER_1000_VND = 1
COINS_PER_100000_VND = 100
STREAK_REWARDS = {
    7: {"xp": 0, "coins": 0},
    14: {"xp": 0, "coins": 0},
    30: {"xp": 0, "coins": 1000},
}
LEVELS = [
    {"name": "Legend", "xp": 70000, "bonus_rate": 0.10, "discount_rate": 0.10},
    {"name": "Elite", "xp": 35000, "bonus_rate": 0.07, "discount_rate": 0.07},
    {"name": "Champion", "xp": 15000, "bonus_rate": 0.05, "discount_rate": 0.05},
    {"name": "Athlete", "xp": 5000, "bonus_rate": 0.03, "discount_rate": 0},
    {"name": "Rookie", "xp": 0, "bonus_rate": 0.01, "discount_rate": 0},
]

users = {}
admin_tokens = {}


def write_json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def cors_response(data=None, status=200):
    response = web.json_response(data or {}, status=status)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def cors_options():
    return web.Response(
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        }
    )


def normalize_phone(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("84") and len(digits) == 11 and digits[2] in "35789":
        return digits
    if digits.startswith("0") and len(digits) == 10 and digits[1] in "35789":
        return "84" + digits[1:]
    if len(digits) == 9 and digits[0] in "35789":
        return "84" + digits
    return ""


def merge_user_records(primary, duplicate):
    merged = {**duplicate, **primary}
    merged["bonus_entries"] = primary.get("bonus_entries", []) + duplicate.get("bonus_entries", [])
    merged["xp"] = int(primary.get("xp", 0)) + int(duplicate.get("xp", 0))
    merged["coins"] = int(primary.get("coins", 0)) + int(duplicate.get("coins", 0))
    merged["orders_count"] = int(primary.get("orders_count", 0)) + int(duplicate.get("orders_count", 0))
    merged["total_spent"] = int(primary.get("total_spent", 0)) + int(duplicate.get("total_spent", 0))
    merged["streak_days"] = max(
        int(primary.get("streak_days", 0)),
        int(duplicate.get("streak_days", 0)),
    )
    merged["welcome_bonus_granted"] = bool(
        primary.get("welcome_bonus_granted", True)
        or duplicate.get("welcome_bonus_granted", True)
    )
    active_bonus_entries(merged)
    return merged


def get_user_by_phone(phone):
    normalized = normalize_phone(phone)
    if not normalized:
        return "", None
    if normalized in users:
        users[normalized]["phone"] = normalized
        return normalized, users[normalized]

    matching_keys = [
        key for key in list(users.keys())
        if normalize_phone(key) == normalized
        or normalize_phone(users[key].get("phone", "")) == normalized
    ]
    if not matching_keys:
        return normalized, None

    merged = users.pop(matching_keys[0])
    for key in matching_keys[1:]:
        merged = merge_user_records(merged, users.pop(key))
    merged["phone"] = normalized
    users[normalized] = merged
    save_users()
    return normalized, users[normalized]


def now_iso():
    return datetime.utcnow().isoformat()


def future_iso(days):
    return (datetime.utcnow() + timedelta(days=days)).isoformat()


def active_bonus_entries(user):
    now = datetime.utcnow()
    entries = []
    for entry in user.get("bonus_entries", []):
        try:
            expires_at = datetime.fromisoformat(entry.get("expires_at", ""))
        except Exception:
            expires_at = now - timedelta(seconds=1)
        amount = int(entry.get("amount", 0))
        if amount > 0 and expires_at >= now:
            entries.append({**entry, "amount": amount})
    if not entries and int(user.get("bonus_balance", 0)) > 0:
        entries.append({
            "amount": int(user.get("bonus_balance", 0)),
            "source": "legacy",
            "expires_at": future_iso(ORDER_BONUS_DAYS),
        })
    entries.sort(key=lambda item: item.get("expires_at", ""))
    user["bonus_entries"] = entries
    user["bonus_balance"] = sum(int(entry.get("amount", 0)) for entry in entries)
    return entries


def add_bonus_entry(user, amount, source, days):
    amount = int(amount)
    if amount <= 0:
        return
    entries = active_bonus_entries(user)
    entries.append({
        "amount": amount,
        "source": source,
        "expires_at": future_iso(days),
    })
    user["bonus_entries"] = entries
    user["bonus_balance"] = sum(int(entry.get("amount", 0)) for entry in entries)


def spend_bonus(user, amount):
    amount = int(amount)
    spent = 0
    entries = active_bonus_entries(user)
    for entry in entries:
        if spent >= amount:
            break
        use = min(int(entry.get("amount", 0)), amount - spent)
        entry["amount"] = int(entry.get("amount", 0)) - use
        spent += use
    user["bonus_entries"] = [entry for entry in entries if int(entry.get("amount", 0)) > 0]
    user["bonus_balance"] = sum(int(entry.get("amount", 0)) for entry in user["bonus_entries"])
    return spent


def level_bonus_rate(level):
    return float(level.get("bonus_rate", 0))


def level_discount_rate(level):
    return float(level.get("discount_rate", 0))


def save_users():
    write_json_file(USERS_FILE, users)


def load_users():
    global users

    if USERS_FILE.exists():
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)


def save_admin_tokens():
    write_json_file(ADMIN_TOKENS_FILE, admin_tokens)


def load_admin_tokens():
    global admin_tokens

    if ADMIN_TOKENS_FILE.exists():
        with open(ADMIN_TOKENS_FILE, "r", encoding="utf-8") as f:
            admin_tokens = json.load(f)


def active_admin_tokens():
    now = datetime.utcnow()
    active = {}
    for token, item in list(admin_tokens.items()):
        try:
            expires_at = datetime.fromisoformat(item.get("expires_at", ""))
        except Exception:
            expires_at = now - timedelta(seconds=1)
        if expires_at >= now:
            active[token] = item
    if active != admin_tokens:
        admin_tokens.clear()
        admin_tokens.update(active)
        save_admin_tokens()
    return active


def issue_admin_token(chat_id):
    token = secrets.token_urlsafe(32)
    active_admin_tokens()
    admin_tokens[token] = {
        "chat_id": str(chat_id),
        "created_at": now_iso(),
        "expires_at": future_iso(30),
    }
    save_admin_tokens()
    return token


def public_user(user):
    active_bonus_entries(user)
    xp = int(user.get("xp", 0))
    level = club_level(xp)
    return {
        "name": user.get("name", ""),
        "surname": user.get("surname", ""),
        "phone": user.get("phone", ""),
        "contact_method": user.get("contact_method", ""),
        "contact_value": user.get("contact_value", ""),
        "bonus_balance": int(user.get("bonus_balance", 0)),
        "xp": xp,
        "level": level["name"],
        "level_min_xp": level["xp"],
        "next_level": next_level(xp),
        "coins": int(user.get("coins", 0)),
        "streak_days": int(user.get("streak_days", 0)),
        "orders_count": int(user.get("orders_count", 0)),
        "total_spent": int(user.get("total_spent", 0)),
        "welcome_bonus": WELCOME_BONUS,
        "bonus_rate": level_bonus_rate(level),
        "discount_rate": level_discount_rate(level),
        "bonus_entries": user.get("bonus_entries", []),
    }


def club_level(xp):
    for level in LEVELS:
        if xp >= level["xp"]:
            return level
    return LEVELS[-1]


def next_level(xp):
    ordered = list(reversed(LEVELS))
    for level in ordered:
        if xp < level["xp"]:
            return level
    return None


def today_key():
    return datetime.utcnow().date().isoformat()


def update_order_streak(user):
    today = today_key()
    last_day = user.get("last_order_date", "")

    if last_day == today:
        return int(user.get("streak_days", 0)), False

    try:
        last_date = datetime.fromisoformat(last_day).date()
        delta = (datetime.utcnow().date() - last_date).days
    except Exception:
        delta = None

    if delta == 1:
        user["streak_days"] = int(user.get("streak_days", 0)) + 1
    else:
        user["streak_days"] = 1

    user["last_order_date"] = today
    return int(user["streak_days"]), True


def loyalty_preview(user, total_value, use_bonus):
    if not user:
        return {
            "registered": False,
            "bonus_applied": 0,
            "bonus_earned": 0,
            "level_discount": 0,
            "bonus_rate": 0,
            "discount_rate": 0,
            "bonus_balance": 0,
            "final_total": total_value,
            "xp_earned": 0,
            "coins_earned": 0,
            "xp": 0,
            "coins": 0,
            "level": "",
            "streak_days": 0,
            "pending": True,
        }

    level = club_level(int(user.get("xp", 0)))
    bonus_rate = level_bonus_rate(level)
    discount_rate = level_discount_rate(level)
    active_bonus_entries(user)
    level_discount = int(total_value * discount_rate)
    discounted_total = max(0, total_value - level_discount)
    bonus_applied = min(int(user.get("bonus_balance", 0)), discounted_total) if use_bonus else 0
    final_total = max(0, discounted_total - bonus_applied)
    bonus_earned = int(final_total * bonus_rate)
    xp_earned = int(final_total / 1000) * XP_PER_1000_VND
    coins_earned = int(final_total / 100000) * COINS_PER_100000_VND

    return {
        "registered": True,
        "bonus_applied": bonus_applied,
        "bonus_earned": bonus_earned,
        "level_discount": level_discount,
        "bonus_rate": bonus_rate,
        "discount_rate": discount_rate,
        "bonus_balance": int(user.get("bonus_balance", 0)),
        "final_total": final_total,
        "xp_earned": xp_earned,
        "coins_earned": coins_earned,
        "xp": int(user.get("xp", 0)),
        "coins": int(user.get("coins", 0)),
        "level": level["name"],
        "streak_days": int(user.get("streak_days", 0)),
        "pending": True,
    }


def apply_loyalty_payment(order_number):
    order = orders.get(order_number)
    if not order or order.get("loyalty_applied"):
        return order.get("loyalty", {}) if order else {}

    phone = order.get("loyalty_phone", "")
    phone, user = get_user_by_phone(phone)
    if not user:
        order["loyalty_applied"] = True
        order["loyalty"] = {"registered": False}
        return order["loyalty"]

    total_value = int(order.get("total_value", order.get("total", 0)) or 0)
    use_bonus = bool(order.get("use_bonus"))
    level_before_data = club_level(int(user.get("xp", 0)))
    level_before = level_before_data["name"]
    bonus_rate = level_bonus_rate(level_before_data)
    discount_rate = level_discount_rate(level_before_data)
    active_bonus_entries(user)
    level_discount = int(total_value * discount_rate)
    discounted_total = max(0, total_value - level_discount)
    bonus_applied = spend_bonus(user, discounted_total) if use_bonus else 0
    final_total = max(0, discounted_total - bonus_applied)
    bonus_earned = int(final_total * bonus_rate)
    add_bonus_entry(user, bonus_earned, "order", ORDER_BONUS_DAYS)
    xp_earned = int(final_total / 1000) * XP_PER_1000_VND
    coins_earned = int(final_total / 100000) * COINS_PER_100000_VND
    streak_days, streak_changed = update_order_streak(user)
    if streak_changed and streak_days in STREAK_REWARDS:
        xp_earned += STREAK_REWARDS[streak_days]["xp"]
        coins_earned += STREAK_REWARDS[streak_days]["coins"]

    user["xp"] = int(user.get("xp", 0)) + xp_earned
    user["coins"] = int(user.get("coins", 0)) + coins_earned
    user["orders_count"] = int(user.get("orders_count", 0)) + 1
    user["total_spent"] = int(user.get("total_spent", 0)) + final_total
    user["updated_at"] = now_iso()
    level_after = club_level(int(user.get("xp", 0)))["name"]

    result = {
        "registered": True,
        "bonus_applied": bonus_applied,
        "bonus_earned": bonus_earned,
        "level_discount": level_discount,
        "bonus_rate": bonus_rate,
        "discount_rate": discount_rate,
        "bonus_balance": int(user.get("bonus_balance", 0)),
        "final_total": final_total,
        "xp_earned": xp_earned,
        "coins_earned": coins_earned,
        "xp": int(user.get("xp", 0)),
        "coins": int(user.get("coins", 0)),
        "level": level_after,
        "streak_days": streak_days,
        "pending": False,
    }
    order["loyalty_applied"] = True
    order["loyalty"] = result
    order["payment_text"] = (
        f"\n\n✅ Оплата подтверждена\n"
        f"🏆 EatFit Club:\n"
        f"Уровень: {level_after}"
        f"{' ↑' if level_before and level_before != level_after else ''}\n"
        f"XP за заказ: +{xp_earned:,}\n"
        f"Начисление уровня: {int(bonus_rate * 100)}%\n\n"
        f"🎁 Бонусы клиента:\n"
        f"Скидка уровня: {level_discount:,} VND\n"
        f"Списано: {bonus_applied:,} VND\n"
        f"Начислено: {bonus_earned:,} VND (срок {ORDER_BONUS_DAYS} дней)\n"
        f"Баланс после оплаты: {user['bonus_balance']:,} VND"
    )
    save_users()
    return result

def save_orders():
    write_json_file(ORDERS_FILE, orders)

def load_orders():
    global orders

    if ORDERS_FILE.exists():
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            orders = json.load(f)


def order_status_map():
    return {
        "new": "🟡 Новый",
        "confirmed": "🔵 Подтвержден",
        "paid": "💳 Оплачен",
        "preparing": "🟠 Готовится",
        "delivery": "🟣 В доставке",
        "done": "🟢 Доставлен",
    }


def parse_money(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits or 0)


def raw_phone_key(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit()) or str(value or "")


def first_legacy_match(pattern, text):
    match = re.search(pattern, text, flags=re.S)
    return match.group(1).strip() if match else ""


def legacy_created_at(order_number):
    match = re.search(r"(\d{8})-(\d{6})", str(order_number or ""))
    if not match:
        return ""
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").isoformat()
    except Exception:
        return ""


def legacy_order_fields(order_number, order):
    text = order.get("order_text", "") or ""
    if not text:
        return {}
    items = first_legacy_match(r"🛒\s*Заказ:\s*(.*?)(?:\n\n💰|\n💰|$)", text)
    total_text = (
        first_legacy_match(r"💰\s*Итого:\s*([^\n]+)", text)
        or first_legacy_match(r"💰\s*Сумма заказа:\s*([^\n]+)", text)
        or first_legacy_match(r"💳\s*К оплате:\s*([^\n]+)", text)
    )
    return {
        "created_at": legacy_created_at(order_number),
        "customer_name": first_legacy_match(r"👤\s*([^\n]+)", text),
        "phone": first_legacy_match(r"📞\s*([^\n]+)", text),
        "address": first_legacy_match(r"🏠\s*([^\n]+)", text),
        "delivery_map": first_legacy_match(r"📍(?:\s*Точка на карте:)?\s*([^\n]+)", text),
        "items": items,
        "total": parse_money(total_text),
        "total_value": parse_money(total_text),
    }


def public_order(order_number, order):
    status = order.get("status", "new")
    status_map = order_status_map()
    legacy = legacy_order_fields(order_number, order)
    return {
        "order_id": order_number,
        "status": status,
        "status_label": status_map.get(status, status),
        "created_at": order.get("created_at", "") or legacy.get("created_at", ""),
        "updated_at": order.get("updated_at", order.get("created_at", "")) or legacy.get("created_at", ""),
        "customer_name": order.get("customer_name", "") or legacy.get("customer_name", ""),
        "phone": order.get("phone", "") or legacy.get("phone", ""),
        "total": int(order.get("total", 0) or legacy.get("total", 0) or 0),
        "total_value": int(order.get("total_value", order.get("total", 0)) or legacy.get("total_value", 0) or 0),
        "items": order.get("items", "") or legacy.get("items", ""),
        "address": order.get("address", "") or legacy.get("address", ""),
        "delivery_map": order.get("delivery_map", "") or legacy.get("delivery_map", ""),
        "contact_method": order.get("contact_method", ""),
        "contact_value": order.get("contact_value", ""),
        "comment": order.get("comment", ""),
        "loyalty": order.get("loyalty", {}),
    }


def admin_token_from_request(request):
    return (request.query.get("token") or request.headers.get("X-Admin-Token") or "").strip()


def admin_authorized(request):
    token = admin_token_from_request(request)
    if ADMIN_TOKEN and secrets.compare_digest(token, ADMIN_TOKEN):
        return True
    return token in active_admin_tokens()


def admin_forbidden_response():
    if not ADMIN_TOKEN:
        text = "Admin access is not configured. Set ADMIN_TOKEN in Render environment variables."
    else:
        text = "Admin token is missing or invalid."
    return web.Response(text=text, status=403, content_type="text/plain")


def admin_order(order_number, order):
    item = public_order(order_number, order)
    item.update({
        "loyalty_phone": order.get("loyalty_phone", ""),
        "loyalty_applied": bool(order.get("loyalty_applied", False)),
        "source": order.get("source", ""),
        "manager_message_id": order.get("manager_message_id"),
        "payment_text": order.get("payment_text", ""),
    })
    return item


def build_admin_database():
    status_map = order_status_map()
    admin_orders = [
        admin_order(order_number, order)
        for order_number, order in orders.items()
    ]
    admin_orders.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    customers = {}
    for phone, user in users.items():
        normalized = normalize_phone(phone or user.get("phone", "")) or raw_phone_key(phone or user.get("phone", ""))
        if not normalized:
            normalized = phone or user.get("phone", "")
        customers.setdefault(normalized, {
            "phone": normalized,
            "name": "",
            "surname": "",
            "registered": False,
            "profile": None,
            "orders": [],
            "orders_count": 0,
            "paid_orders_count": 0,
            "total_orders_value": 0,
            "total_paid_value": 0,
            "last_order_at": "",
            "statuses": {},
        })
        customers[normalized]["registered"] = True
        customers[normalized]["profile"] = public_user(user)
        customers[normalized]["name"] = user.get("name", "")
        customers[normalized]["surname"] = user.get("surname", "")

    for item in admin_orders:
        phone = (
            normalize_phone(item.get("phone", ""))
            or normalize_phone(item.get("loyalty_phone", ""))
            or raw_phone_key(item.get("phone", ""))
            or raw_phone_key(item.get("loyalty_phone", ""))
        )
        if not phone:
            phone = f"no-phone:{item.get('customer_name', 'unknown')}"
        customer = customers.setdefault(phone, {
            "phone": phone,
            "name": "",
            "surname": "",
            "registered": False,
            "profile": None,
            "orders": [],
            "orders_count": 0,
            "paid_orders_count": 0,
            "total_orders_value": 0,
            "total_paid_value": 0,
            "last_order_at": "",
            "statuses": {},
        })
        if item.get("customer_name") and not (customer.get("name") or customer.get("surname")):
            parts = item.get("customer_name", "").split()
            customer["name"] = parts[0] if parts else ""
            customer["surname"] = " ".join(parts[1:]) if len(parts) > 1 else ""
        customer["orders"].append(item)
        customer["orders_count"] += 1
        customer["total_orders_value"] += int(item.get("total_value") or item.get("total") or 0)
        if item.get("status") in ("paid", "done"):
            customer["paid_orders_count"] += 1
            customer["total_paid_value"] += int(item.get("total") or 0)
        status = item.get("status", "new")
        customer["statuses"][status] = customer["statuses"].get(status, 0) + 1
        if item.get("created_at") and item.get("created_at") > customer.get("last_order_at", ""):
            customer["last_order_at"] = item.get("created_at")

    customer_list = list(customers.values())
    customer_list.sort(key=lambda item: item.get("last_order_at") or "", reverse=True)

    return {
        "success": True,
        "version": APP_VERSION,
        "generated_at": now_iso(),
        "summary": {
            "customers": len(customer_list),
            "registered_customers": sum(1 for item in customer_list if item.get("registered")),
            "orders": len(admin_orders),
            "paid_or_done_orders": sum(1 for item in admin_orders if item.get("status") in ("paid", "done")),
            "total_orders_value": sum(int(item.get("total_value") or item.get("total") or 0) for item in admin_orders),
            "total_paid_value": sum(int(item.get("total") or 0) for item in admin_orders if item.get("status") in ("paid", "done")),
        },
        "status_labels": status_map,
        "customers": customer_list,
        "orders": admin_orders,
    }


def build_status_keyboard(order_number, current_status=""):
    buttons = [
        ("confirmed", "🔵 Подтвердить"),
        ("paid", "💳 Оплачен"),
        ("preparing", "🟠 Готовится"),
        ("delivery", "🟣 В доставке"),
        ("done", "🟢 Доставлен"),
    ]
    current_index = next(
        (index for index, item in enumerate(buttons) if item[0] == current_status),
        -1,
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"orderstatus_{status}|{order_number}")]
        for index, (status, label) in enumerate(buttons)
        if index > current_index
    ])


def clean_order_message_text(text):
    if text.startswith("📌 Текущий статус:\n"):
        parts = text.split("\n\n", 1)
        if len(parts) == 2:
            text = parts[1]
    for marker in ("\n\n📌 Текущий статус:", "\n\n📌 Статус:"):
        if marker in text:
            return text.split(marker)[0]
    return text


def order_text_with_status(order, status):
    status_map = order_status_map()
    base_text = clean_order_message_text(order.get("order_text", ""))
    payment_text = order.get("payment_text", "")
    if payment_text and payment_text not in base_text:
        base_text += payment_text
    return f"📌 Текущий статус:\n{status_map.get(status, status)}\n\n{base_text}"


async def update_order_status_message(order_number, status, bot, chat_id=None, message_id=None, fallback_text=""):
    if order_number not in orders:
        orders[order_number] = {
            "user_id": None,
            "status": "new",
            "source": "status_link",
            "order_text": clean_order_message_text(fallback_text or ""),
            "manager_message_id": message_id,
            "created_at": now_iso(),
        }

    order = orders[order_number]
    order["status"] = status
    order["updated_at"] = now_iso()
    if status in ("paid", "done"):
        apply_loyalty_payment(order_number)
        order = orders[order_number]
        order["updated_at"] = now_iso()
    save_orders()

    updated_text = order_text_with_status(order, status)
    updated_keyboard = build_status_keyboard(order_number, status)
    target_chat_id = chat_id or ORDER_CHAT_ID
    target_message_id = order.get("manager_message_id") or message_id

    if target_message_id:
        await bot.edit_message_text(
            chat_id=target_chat_id,
            message_id=target_message_id,
            text=updated_text,
            reply_markup=updated_keyboard,
        )
    else:
        sent_message = await bot.send_message(
            chat_id=target_chat_id,
            text=updated_text,
            reply_markup=updated_keyboard,
        )
        order["manager_message_id"] = sent_message.message_id
        save_orders()

    return updated_text



def build_order_preview(user_id):
    products = get_products()
    products_map = {p["id"]: p for p in products}

    cart = carts.get(user_id, {})

    total = 0
    total_protein = 0
    total_fat = 0
    total_carbs = 0
    total_calories = 0
    lines = []

    for product_id, qty in cart.items():
        product = products_map.get(product_id)
        if not product:
            continue

        line_total = product["price"] * qty
        total += line_total

        total_protein += product["protein"] * qty
        total_fat += product["fat"] * qty
        total_carbs += product["carbs"] * qty
        total_calories += product["calories"] * qty

        lines.append(
            f"{product['name']} × {qty}\n{line_total:,} VND"
        )

    data = order_data[user_id]

    text = (
        "📋 Проверьте данные заказа\n\n"
        f"👤 {data['name']}\n\n"
        f"📞 {data['phone']}\n\n"
        f"🏠 {data['address']}\n\n"
        f"📍 {data['maps']}\n\n"
        "🛒 Ваш заказ:\n\n"
        + "\n\n".join(lines)
        + (
            f"\n\n────────────────\n\n"
            f"🥩 Белки: {total_protein} г\n"
            f"🥑 Жиры: {total_fat} г\n"
            f"🍚 Углеводы: {total_carbs} г\n"
            f"🔥 Калории: {total_calories} ккал"
            f"\n\n────────────────\n\n"
            f"💰 Итого: {total:,} VND"
        )
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
    ])

    return text, keyboard


def get_cart_text_and_keyboard(user_id):
    products = get_products()
    products_map = {p["id"]: p for p in products}

    cart = carts.get(user_id, {})

    if not cart:
        return "🛒 Корзина пуста", None

    text = "🛒 Корзина\n\n"
    total = 0
    total_protein = 0
    total_fat = 0
    total_carbs = 0
    total_calories = 0
    keyboard = []

    for idx, (product_id, qty) in enumerate(cart.items(), start=1):
        product = products_map.get(product_id)

        if not product:
            continue

        line_total = product["price"] * qty
        total += line_total

        total_protein += product["protein"] * qty
        total_fat += product["fat"] * qty
        total_carbs += product["carbs"] * qty
        total_calories += product["calories"] * qty

        text += (
            f"{idx}️⃣ {product['name']} × {qty}\n"
            f"{line_total:,} VND\n\n"
        )

        keyboard.append([
            InlineKeyboardButton("➖", callback_data=f"minus_{product_id}"),
            InlineKeyboardButton(f"x{qty}", callback_data="ignore"),
            InlineKeyboardButton("➕", callback_data=f"plus_{product_id}")
        ])

    text += "────────────────\n\n"
    text += (
        f"🥩 Белки: {total_protein} г\n"
        f"🥑 Жиры: {total_fat} г\n"
        f"🍚 Углеводы: {total_carbs} г\n"
        f"🔥 Калории: {total_calories} ккал\n\n"
    )
    text += "────────────────\n\n"
    text += f"Итого: {total:,} VND"

    keyboard.append([
        InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")
    ])

    keyboard.append([
        InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")
    ])

    return text, InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🍽 Открыть меню", callback_data="menu")]
    ])

    with open("assets/logo.jpg", "rb") as logo:
        await update.message.reply_photo(
            photo=logo,
            caption=(
                "🥗 EatFit Vietnam\n\n"
                "Правильное питание без готовки.\n\n"
                "💪 Высокобелковые блюда\n"
                "🔥 Подсчитанные КБЖУ\n"
                "🥗 Свежие ингредиенты\n"
                "🚚 Доставка по всему Вьетнаму\n\n"
                "Выберите блюда из меню ниже 👇"
            ),
            reply_markup=keyboard
        )

    await update.message.reply_text(
        "👇 Быстрые действия",
        reply_markup=get_main_keyboard()
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_catalog(query, context)


async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.replace("catalog_", ""))
    await show_product(query, context, index)


async def add_to_cart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    index = int(query.data.replace("add_", ""))

    products = get_products()
    product = products[index]

    user_id = query.from_user.id

    if user_id not in carts:
        carts[user_id] = {}

    product_id = product["id"]
    carts[user_id][product_id] = carts[user_id].get(product_id, 0) + 1

    cart_count = sum(carts[user_id].values())

    from telegram import InputMediaPhoto

    caption = build_caption(product)
    photo = f"assets/{product['images'][0]}"

    try:
        with open(photo, "rb") as image:
            media = InputMediaPhoto(media=image, caption=caption)

            await query.edit_message_media(
                media=media,
                reply_markup=build_keyboard(index, len(products), cart_count)
            )
    except Exception as e:
        print("KBJU EDIT ERROR:", e)

    return


async def cart_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text, keyboard = get_cart_text_and_keyboard(query.from_user.id)

    await query.message.reply_text(text, reply_markup=keyboard)


async def plus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.replace("plus_", "")
    user_id = query.from_user.id

    carts[user_id][product_id] += 1

    text, keyboard = get_cart_text_and_keyboard(user_id)
    await query.edit_message_text(text, reply_markup=keyboard)


async def minus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = query.data.replace("minus_", "")
    user_id = query.from_user.id

    carts[user_id][product_id] -= 1

    if carts[user_id][product_id] <= 0:
        del carts[user_id][product_id]

    text, keyboard = get_cart_text_and_keyboard(user_id)

    if keyboard:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await query.edit_message_text(text)


async def clear_cart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    carts[query.from_user.id] = {}

    await query.edit_message_text("🛒 Корзина очищена")


async def checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    order_data[user_id] = {
        "step": "name"
    }

    await query.message.reply_text(
        "👤 Введите ФИО\n\nEnter your full name"
    )



async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_map = order_status_map()

    lines = ["📦 Активные заказы\n"]

    found = False

    for order_number, order in orders.items():
        if order.get("status") == "done":
            continue

        found = True

        lines.append(
            f"{order_number}\n{status_map.get(order.get('status'), order.get('status'))}\n"
        )

    if not found:
        await update.message.reply_text("📦 Нет активных заказов")
        return

    await update.message.reply_text("\n".join(lines))



async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_orders = len(orders)

    stats = {
        "new": 0,
        "confirmed": 0,
        "paid": 0,
        "preparing": 0,
        "delivery": 0,
        "done": 0
    }

    revenue = 0

    for order in orders.values():
        status = order.get("status", "new")
        if status in stats:
            stats[status] += 1

        order_text = order.get("order_text", "")
        if "💰 Итого:" in order_text:
            try:
                amount = order_text.split("💰 Итого:")[1].split("VND")[0]
                amount = int(amount.replace(",", "").strip())
                revenue += amount
            except:
                pass

    message = (
        f"📊 Статистика\n\n"
        f"Всего заказов: {total_orders}\n\n"
        f"🟡 Новые: {stats['new']}\n"
        f"🔵 Подтверждено: {stats['confirmed']}\n"
        f"💳 Оплачено: {stats['paid']}\n"
        f"🟠 Готовится: {stats['preparing']}\n"
        f"🟣 В доставке: {stats['delivery']}\n"
        f"🟢 Доставлено: {stats['done']}\n\n"
        f"💰 Общая сумма:\n{revenue:,} VND"
    )

    await update.message.reply_text(message)


async def cart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, keyboard = get_cart_text_and_keyboard(update.effective_user.id)

    await update.message.reply_text(text, reply_markup=keyboard)


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(
        f"Chat ID для заказов:\n{chat.id}\n\n"
        "Добавьте это значение в Render как ORDER_CHAT_ID."
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.id != ORDER_CHAT_ID:
        await update.message.reply_text("Админ-доступ доступен только в рабочем чате заказов EatFit.")
        return

    token = issue_admin_token(chat.id)
    await update.message.reply_text(
        "🔐 Кабинет владельца EatFit\n\n"
        "Ссылка действует 30 дней. Не отправляйте ее клиентам.\n\n"
        f"{PUBLIC_BASE_URL}/admin?token={quote(token)}"
    )




async def kbju_edit_message(context, user_id, text_value, reply_markup=None):
    data = kbju_data.get(user_id)
    if not data:
        return
    try:
        await context.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            text=text_value,
            reply_markup=reply_markup
        )
    except Exception as e:
        print("KBJU EDIT ERROR:", e)
        try:
            msg = await context.bot.send_message(
                chat_id=data["chat_id"],
                text=text_value,
                reply_markup=reply_markup
            )
            data["message_id"] = msg.message_id
        except Exception as e2:
            print("KBJU SEND ERROR:", e2)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in kbju_data:
        data = kbju_data[user_id]
        txt = update.message.text

        if "cleanup_messages" not in data:
            data["cleanup_messages"] = []
        data["cleanup_messages"].append(update.message.message_id)

        if data["step"] == "gender":
            data["gender"] = txt.lower()
            data["step"] = "age"

            msg = await update.message.reply_text(
                "📊 Расчет КБЖУ\n\nШаг 2 из 6\n\nВведите возраст:"
            )
            data["cleanup_messages"].append(msg.message_id)
            return

        elif data["step"] == "age":

            if not txt.strip().isdigit():
                return

            try:
                await update.message.delete()
            except:
                pass

            data["age"] = int(txt.strip())
            data["step"] = "height"

            await kbju_edit_message(
                context,
                user_id,
                "📊 Расчет КБЖУ\n\nШаг 3 из 6\n\nВведите рост (см):"
            )
            return

        elif data["step"] == "height":
            data["height"] = int(txt.strip())
            data["step"] = "weight"
            msg = await update.message.reply_text("Вес (кг)?")
            data["cleanup_messages"].append(msg.message_id)
            return

        elif data["step"] == "weight":
            data["weight"] = float(txt.replace(",", ".").strip())
            data["step"] = "goal"
            goal_kb = ReplyKeyboardMarkup([["🔥 Похудение"],["⚖️ Поддержание"],["💪 Набор массы"]], resize_keyboard=True)
            msg = await update.message.reply_text("Выберите цель:", reply_markup=goal_kb)
            data["cleanup_messages"].append(msg.message_id)
            return

        elif data["step"] == "goal":
            data["goal"] = txt.lower()
            data["step"] = "activity"
            activity_kb = ReplyKeyboardMarkup(
                [["🚶 Низкая"],["🏃 Средняя"],["🔥 Высокая"]],
                resize_keyboard=True
            )
            msg = await update.message.reply_text(
                "Выберите активность:\n\n🚶 Низкая - до 7000 шагов\n🏃 Средняя - 7000-12000 шагов или 2-4 тренировки\n🔥 Высокая - 12000+ шагов или 5+ тренировок",
                reply_markup=activity_kb
            )
            data["cleanup_messages"].append(msg.message_id)
            return

        elif data["step"] == "activity":
            act = txt.lower()
            factor = 1.2
            if "сред" in act:
                factor = 1.55
            elif "выс" in act:
                factor = 1.75

            bmr = 10*data["weight"] + 6.25*data["height"] - 5*data["age"]
            bmr += 5 if "муж" in data["gender"] else -161

            calories = bmr * factor

            if "пох" in data["goal"]:
                calories *= 0.8
            elif "набор" in data["goal"]:
                calories *= 1.15

            protein = round(data["weight"]*2)
            fat = round(data["weight"]*0.8)
            carbs = round((calories - protein*4 - fat*9)/4)

            try:
                await update.message.delete()
            except:
                pass

            for mid in data.get("cleanup_messages", []):
                try:
                    await context.bot.delete_message(
                        chat_id=data["chat_id"],
                        message_id=mid
                    )
                except:
                    pass

            try:
                await context.bot.delete_message(
                    chat_id=data["chat_id"],
                    message_id=data["message_id"]
                )
            except:
                pass

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    f"📊 Ваша норма:\n\n"
                    f"🔥 Калории: {round(calories)} ккал\n"
                    f"🥩 Белки: {protein} г\n"
                    f"🥑 Жиры: {fat} г\n"
                    f"🍚 Углеводы: {carbs} г"
                )
            )

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="✅ Расчёт завершён.\n\nВыберите действие:",
                reply_markup=get_main_keyboard()
            )

            del kbju_data[user_id]
            return

    if user_id not in order_data:
        return

    step = order_data[user_id].get("step")

    if step == "name":
        order_data[user_id]["name"] = update.message.text
        order_data[user_id]["step"] = "phone"

        phone_keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Отправить номер телефона", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )

        await update.message.reply_text(
            "📱 Отправьте номер телефона\n\nShare your phone number",
            reply_markup=phone_keyboard
        )

    elif step == "address":
        order_data[user_id]["address"] = update.message.text
        order_data[user_id]["step"] = "maps"

        await update.message.reply_text(
            "📍 Отправьте ссылку Google Maps\n\nSend your Google Maps location link"
        )

    elif step == "maps":
        order_data[user_id]["maps"] = update.message.text
        order_data[user_id]["step"] = "confirm"

        text_preview, keyboard = build_order_preview(user_id)

        await update.message.reply_text(
            text_preview,
            reply_markup=keyboard
        )


async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in order_data:
        return

    if order_data[user_id].get("step") != "phone":
        return

    order_data[user_id]["phone"] = update.message.contact.phone_number
    order_data[user_id]["step"] = "address"

    await update.message.reply_text(
        "✅ Телефон сохранен\n\nPhone number saved\n\n🏠 Введите полный адрес доставки\n\nEnter your full delivery address"
    )


async def confirm_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    order_number = datetime.now().strftime(
        "EATFIT-%Y%m%d-%H%M%S"
    )

    orders[order_number] = {
        "user_id": user_id,
        "status": "new",
        "order_text": "",
        "manager_message_id": None
    }

    save_orders()

    products = get_products()
    products_map = {p["id"]: p for p in products}

    cart = carts.get(user_id, {})

    total = 0
    items = []

    for product_id, qty in cart.items():
        product = products_map.get(product_id)

        if not product:
            continue

        line_total = product["price"] * qty
        total += line_total

        items.append(
            f"{product['name']} × {qty} = {line_total:,} VND"
        )

    data = order_data[user_id]

    status_keyboard = build_status_keyboard(order_number)

    order_text = (
        f"🔔 Новый заказ\n"
        f"№ {order_number}\n\n"
        f"👤 {data['name']}\n\n"
        f"📞 {data['phone']}\n\n"
        f"🏠 {data['address']}\n\n"
        f"📍 {data['maps']}\n\n"
        "🛒 Заказ:\n\n"
        + "\n".join(items)
        + f"\n\n💰 Итого: {total:,} VND"
    )

    try:
        sent_message = await context.bot.send_message(
            chat_id=ORDER_CHAT_ID,
            text=order_text_with_status(orders[order_number], "new"),
            reply_markup=build_status_keyboard(order_number, "new")
        )

        orders[order_number]["manager_message_id"] = sent_message.message_id
        orders[order_number]["order_text"] = order_text
        save_orders()

        print("ORDER SENT")
        print(orders)

    except Exception as e:
        print("SEND ERROR:", e)

    carts[user_id] = {}

    if user_id in order_data:
        del order_data[user_id]

    await query.message.reply_text(
        f"✅ Заказ успешно оформлен!\n\n"
        f"Thank you for your order!\n\n"
        f"Номер заказа / Order number:\n{order_number}\n\n"
        f"Наш менеджер свяжется с вами в ближайшее время для подтверждения заказа.\n\n"
        f"Our manager will contact you shortly."
    )



async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    data = query.data.split("|")
    status = data[0].replace("orderstatus_", "").replace("status_", "")
    order_number = data[1]
    status_map = order_status_map()
    await query.answer(f"Меняю статус: {status_map.get(status, status)}")

    if order_number not in orders:
        orders[order_number] = {
            "user_id": None,
            "status": "new",
            "source": "telegram_message",
            "order_text": clean_order_message_text(query.message.text or ""),
            "manager_message_id": query.message.message_id,
            "created_at": now_iso(),
        }

    user_id = orders[order_number].get("user_id")
    orders[order_number]["status"] = status
    orders[order_number]["updated_at"] = now_iso()
    if status in ("paid", "done"):
        apply_loyalty_payment(order_number)
        orders[order_number]["updated_at"] = now_iso()
    save_orders()

    if user_id:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"📦 Обновление заказа\n\n"
                f"Заказ: {order_number}\n\n"
                f"Статус: {status_map[status]}"
            )
        )

    try:
        try:
            await update_order_status_message(
                order_number,
                status,
                context.bot,
                chat_id=query.message.chat.id,
                message_id=query.message.message_id,
                fallback_text=query.message.text or "",
            )
        except Exception as edit_error:
            print("STATUS DIRECT EDIT ERROR:", edit_error)
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=order_text_with_status(orders[order_number], status),
                reply_markup=build_status_keyboard(order_number, status),
            )
            orders[order_number]["manager_message_id"] = sent_message.message_id
            save_orders()
    except Exception as e:
        print("STATUS UPDATE ERROR:", e)


async def cancel_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if user_id in order_data:
        del order_data[user_id]

    await query.edit_message_text(
        "❌ Оформление заказа отменено"
    )



async def main_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text

    if txt == "🍽 Меню":
        products = get_products()
        class SimpleMessage:
            def __init__(self, message):
                self.message = message

        fake_query = SimpleMessage(update.message)
        await show_product(fake_query, context, 0)

    elif txt == "🛍 Корзина":
        await cart_command(update, context)

    elif txt == "📦 Мои заказы":
        await orders_command(update, context)

    elif txt == "📊 Рассчитать КБЖУ":
        gender_kb = ReplyKeyboardMarkup([["👨 Мужчина","👩 Женщина"]], resize_keyboard=True)

        msg = await update.message.reply_text(
            "📊 Расчет КБЖУ\n\nШаг 1 из 6\n\nВыберите пол:",
            reply_markup=gender_kb
        )

        kbju_data[update.effective_user.id] = {
            "step": "gender",
            "message_id": msg.message_id,
            "chat_id": msg.chat_id,
            "cleanup_messages": [msg.message_id]
        }

    elif txt == "💬 Связаться с менеджером":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Открыть чат с менеджером", url="https://t.me/max_zoti_kov")]
        ])
        await update.message.reply_text(
            "Связь с менеджером:",
            reply_markup=keyboard
        )




async def site_order(request):
    if request.method == "OPTIONS":
        return cors_options()

    try:
        data = await request.json()
        contact_labels = {
            "zalo": "Zalo",
            "whatsapp": "WhatsApp",
            "telegram": "Telegram",
        }
        contact_method = data.get("contact_method", "")
        contact_value = data.get("contact_value", "") or data.get("telegram", "")
        contact_label = contact_labels.get(contact_method, contact_method)
        contact_line = (
            f"\n💬 Удобная связь: {contact_label} — {contact_value}\n"
            if contact_label or contact_value
            else ""
        )
        map_value = data.get("delivery_map", "") or data.get("maps", "")
        map_line = f"\n📍 Точка на карте: {map_value}\n" if map_value else ""

        print("SITE ORDER RECEIVED")
        print(data)
        customer_phone = normalize_phone(data.get("phone"))
        if not customer_phone:
            return cors_response({"success": False, "error": "invalid_phone"}, status=400)

        customer_name = " ".join(
            part for part in [data.get("name", ""), data.get("surname", "")]
            if part
        )
        order_number = data.get("order_id") or datetime.now().strftime("SITE-%Y%m%d-%H%M%S")

        requested_loyalty_phone = str(data.get("loyalty_phone") or "").strip()
        loyalty_phone = ""
        loyalty_user = None
        if requested_loyalty_phone:
            normalized_loyalty_phone = normalize_phone(requested_loyalty_phone)
            if not normalized_loyalty_phone:
                return cors_response({"success": False, "error": "invalid_loyalty_phone"}, status=400)
            loyalty_phone, loyalty_user = get_user_by_phone(normalized_loyalty_phone)
        else:
            loyalty_phone, loyalty_user = get_user_by_phone(customer_phone)
        if not loyalty_user:
            loyalty_phone = ""
        use_bonus = bool(data.get("use_bonus"))
        total_value = int(float(data.get("total") or 0))
        loyalty_result = loyalty_preview(loyalty_user, total_value, use_bonus)
        bonus_applied = loyalty_result["bonus_applied"]
        bonus_earned = loyalty_result["bonus_earned"]
        level_discount = loyalty_result["level_discount"]
        bonus_rate = loyalty_result["bonus_rate"]
        final_total = loyalty_result["final_total"]
        loyalty_line = ""

        if loyalty_user:
            loyalty_user["contact_method"] = contact_method or loyalty_user.get("contact_method", "")
            loyalty_user["contact_value"] = contact_value or loyalty_user.get("contact_value", "")
            loyalty_user["updated_at"] = now_iso()
            save_users()
            loyalty_line = (
                f"\n🏆 EatFit Club:\n"
                f"Уровень: {loyalty_result['level']}\n"
                f"Начисление уровня: {int(bonus_rate * 100)}%\n\n"
                f"🎁 После статуса «Оплачен»:\n"
                f"Скидка уровня: {level_discount:,} VND\n"
                f"Будет списано бонусов: {bonus_applied:,} VND\n"
                f"Будет начислено: {bonus_earned:,} VND (срок {ORDER_BONUS_DAYS} дней)\n"
                f"Текущий баланс: {loyalty_user['bonus_balance']:,} VND\n"
            )

        text_order = (
            f"🔔 Новый заказ с сайта\n\n"
            f"№ {order_number}\n\n"
            f"👤 {customer_name}\n\n"
            f"📞 {customer_phone}\n\n"
            f"{contact_line}"
            f"🏠 {data.get('address','')}\n\n"
            f"{map_line}"
            f"🛒 Заказ:\n\n"
            f"{data.get('items','')}\n\n"
            f"💰 Сумма заказа: {total_value:,} VND\n"
            f"🏷 Скидка уровня: {level_discount:,} VND\n"
            f"🎁 Списано бонусов: {bonus_applied:,} VND\n"
            f"💳 К оплате: {final_total:,} VND"
            f"{loyalty_line}"
        )
        orders[order_number] = {
            "user_id": None,
            "status": "new",
            "source": "site",
            "order_text": text_order,
            "manager_message_id": None,
            "customer_name": customer_name,
            "phone": customer_phone,
            "address": data.get("address", ""),
            "delivery_map": map_value,
            "contact_method": contact_method,
            "contact_value": contact_value,
            "comment": data.get("comment", ""),
            "items": data.get("items", ""),
            "total": final_total,
            "total_value": total_value,
            "loyalty_phone": loyalty_phone,
            "use_bonus": use_bonus,
            "loyalty_applied": False,
            "loyalty": loyalty_result,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

        sent_message = await telegram_app.bot.send_message(
            chat_id=ORDER_CHAT_ID,
            text=order_text_with_status(orders[order_number], "new"),
            reply_markup=build_status_keyboard(order_number, "new")
        )
        orders[order_number]["manager_message_id"] = sent_message.message_id
        save_orders()

        print("SITE ORDER SENT TO TELEGRAM")

        return cors_response({
            "success": True,
            "loyalty": loyalty_result,
        })
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return cors_response({"success": False, "error": str(e)})


async def loyalty_register(request):
    if request.method == "OPTIONS":
        return cors_options()

    try:
        data = await request.json()
        phone, user = get_user_by_phone(data.get("phone"))
        if not phone:
            return cors_response({"success": False, "error": "invalid_phone"}, status=400)

        is_new = user is None
        user = user or {}
        welcome_bonus_granted = bool(user.get("welcome_bonus_granted", False))
        users[phone] = {
            **user,
            "name": data.get("name", user.get("name", "")),
            "surname": data.get("surname", user.get("surname", "")),
            "phone": phone,
            "contact_method": data.get("contact_method", user.get("contact_method", "")),
            "contact_value": data.get("contact_value", user.get("contact_value", "")),
            "bonus_balance": int(user.get("bonus_balance", 0)),
            "bonus_entries": user.get("bonus_entries", []),
            "xp": int(user.get("xp", 0)),
            "coins": int(user.get("coins", 0)),
            "streak_days": int(user.get("streak_days", 0)),
            "last_order_date": user.get("last_order_date", ""),
            "orders_count": int(user.get("orders_count", 0)),
            "total_spent": int(user.get("total_spent", 0)),
            "created_at": user.get("created_at", now_iso()),
            "updated_at": now_iso(),
            "welcome_bonus_granted": welcome_bonus_granted,
        }
        if is_new and not welcome_bonus_granted:
            add_bonus_entry(users[phone], WELCOME_BONUS, "welcome", WELCOME_BONUS_DAYS)
            users[phone]["welcome_bonus_granted"] = True
        else:
            active_bonus_entries(users[phone])
        save_users()

        return cors_response({
            "success": True,
            "is_new": is_new,
            "user": public_user(users[phone]),
        })
    except Exception as e:
        return cors_response({"success": False, "error": str(e)}, status=500)


async def loyalty_status(request):
    if request.method == "OPTIONS":
        return cors_options()

    phone = normalize_phone(request.query.get("phone", ""))
    if not phone and request.method == "POST":
        try:
            data = await request.json()
            phone = normalize_phone(data.get("phone"))
        except Exception:
            phone = ""

    phone, user = get_user_by_phone(phone)
    if not phone:
        return cors_response({"success": False, "error": "invalid_phone"}, status=400)
    return cors_response({
        "success": True,
        "registered": bool(user),
        "user": public_user(user) if user else None,
        "welcome_bonus": WELCOME_BONUS,
        "bonus_rate": level_bonus_rate(club_level(int(user.get("xp", 0)))) if user else level_bonus_rate(LEVELS[-1]),
    })


async def loyalty_orders(request):
    if request.method == "OPTIONS":
        return cors_options()

    phone = normalize_phone(request.query.get("phone", ""))
    if not phone and request.method == "POST":
        try:
            data = await request.json()
            phone = normalize_phone(data.get("phone"))
        except Exception:
            phone = ""

    if not phone:
        return cors_response({"success": False, "error": "invalid_phone"}, status=400)

    matched_orders = []
    for order_number, order in orders.items():
        order_phone = normalize_phone(order.get("phone", ""))
        loyalty_phone = normalize_phone(order.get("loyalty_phone", ""))
        if phone in (order_phone, loyalty_phone):
            if order.get("status") in ("paid", "done") and order.get("loyalty_phone") and not order.get("loyalty_applied"):
                apply_loyalty_payment(order_number)
                order = orders.get(order_number, order)
            matched_orders.append(public_order(order_number, order))

    matched_orders.sort(
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )
    return cors_response({"success": True, "orders": matched_orders})


async def order_status_web(request):
    if request.method == "OPTIONS":
        return cors_options()

    try:
        if request.method == "POST":
            data = await request.json()
            order_number = data.get("order", "")
            status = data.get("status", "")
        else:
            order_number = request.query.get("order", "")
            status = request.query.get("status", "")

        status_map = order_status_map()
        if not order_number or status not in status_map:
            return web.Response(
                text="Invalid order status request",
                status=400,
                content_type="text/plain",
            )

        await update_order_status_message(order_number, status, telegram_app.bot)
        status_label = status_map[status]
        return web.Response(
            text=(
                "<!doctype html><meta charset='utf-8'>"
                "<body style='font-family:Arial,sans-serif;padding:32px;line-height:1.4'>"
                f"<h2>Статус обновлён</h2>"
                f"<p>Заказ: <b>{order_number}</b></p>"
                f"<p>Новый статус: <b>{status_label}</b></p>"
                "<p>Можно вернуться в Telegram.</p>"
                "</body>"
            ),
            content_type="text/html",
        )
    except Exception as e:
        print("ORDER STATUS WEB ERROR:", e)
        return web.Response(
            text=f"Status update error: {e}",
            status=500,
            content_type="text/plain",
        )



async def test(request):
    try:
        await telegram_app.bot.send_message(
            chat_id=ORDER_CHAT_ID,
            text=f"✅ TEST MESSAGE FROM RENDER\nVersion: {APP_VERSION}"
        )
        return web.json_response({"success": True, "version": APP_VERSION})
    except Exception as e:
        return web.json_response({"success": False, "version": APP_VERSION, "error": str(e)})


async def version(request):
    return web.json_response({
        "success": True,
        "version": APP_VERSION,
        "data_dir": str(DATA_DIR),
        "orders_file_exists": ORDERS_FILE.exists(),
        "users_file_exists": USERS_FILE.exists(),
    })


async def admin_data(request):
    if request.method == "OPTIONS":
        return cors_options()
    if not admin_authorized(request):
        return admin_forbidden_response()
    return cors_response(build_admin_database())


async def admin_dashboard(request):
    if not admin_authorized(request):
        return admin_forbidden_response()

    data = build_admin_database()
    summary = data["summary"]
    token = quote(admin_token_from_request(request))

    customer_rows = []
    for customer in data["customers"]:
        profile = customer.get("profile") or {}
        display_name = " ".join(part for part in [customer.get("name"), customer.get("surname")] if part) or "—"
        status_text = ", ".join(
            f"{data['status_labels'].get(status, status)}: {count}"
            for status, count in customer.get("statuses", {}).items()
        ) or "—"
        customer_rows.append(
            "<tr>"
            f"<td>{escape(display_name)}</td>"
            f"<td>{escape(customer.get('phone', ''))}</td>"
            f"<td>{'Да' if customer.get('registered') else 'Нет'}</td>"
            f"<td>{escape(profile.get('level', '—'))}</td>"
            f"<td>{int(profile.get('xp', 0)):,}</td>"
            f"<td>{int(profile.get('bonus_balance', 0)):,} VND</td>"
            f"<td>{customer.get('orders_count', 0)}</td>"
            f"<td>{customer.get('paid_orders_count', 0)}</td>"
            f"<td>{int(customer.get('total_orders_value', 0)):,} VND</td>"
            f"<td>{escape(status_text)}</td>"
            f"<td>{escape(customer.get('last_order_at', ''))}</td>"
            "</tr>"
        )

    order_rows = []
    for order in data["orders"]:
        loyalty = order.get("loyalty") or {}
        order_rows.append(
            "<tr>"
            f"<td>{escape(order.get('created_at', ''))}</td>"
            f"<td>{escape(order.get('order_id', ''))}</td>"
            f"<td>{escape(order.get('status_label', order.get('status', '')))}</td>"
            f"<td>{escape(order.get('customer_name', ''))}</td>"
            f"<td>{escape(order.get('phone', ''))}</td>"
            f"<td>{escape(order.get('contact_method', ''))}: {escape(order.get('contact_value', ''))}</td>"
            f"<td>{int(order.get('total_value') or 0):,} VND</td>"
            f"<td>{int(order.get('total') or 0):,} VND</td>"
            f"<td>{'Да' if loyalty.get('registered') else 'Нет'}</td>"
            f"<td>{int(loyalty.get('bonus_applied', 0)):,} / {int(loyalty.get('bonus_earned', 0)):,} VND</td>"
            f"<td>{int(loyalty.get('xp_earned', 0)):,}</td>"
            f"<td>{escape(order.get('address', ''))}</td>"
            f"<td><pre>{escape(order.get('items', '').strip())}</pre></td>"
            "</tr>"
        )

    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EatFit Admin</title>
<style>
body{{font-family:Arial,sans-serif;margin:0;background:#f6f8f3;color:#172015}}
header{{position:sticky;top:0;z-index:2;padding:18px 24px;background:#102015;color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.16)}}
h1{{margin:0 0 6px;font-size:26px}} a{{color:#317d20;font-weight:700}} header a{{color:#dff7d3}}
main{{padding:22px;max-width:1500px;margin:auto}} .stats{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-bottom:18px}}
.stat{{padding:14px;background:#fff;border:1px solid #dfe8da;border-radius:14px}} .stat b{{display:block;font-size:22px;color:#172015}} .stat span{{color:#667461;font-size:12px}}
section{{margin:18px 0;padding:18px;background:#fff;border:1px solid #dfe8da;border-radius:18px;overflow:auto}}
h2{{margin:0 0 12px}} table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{padding:9px;border-bottom:1px solid #edf2e9;text-align:left;vertical-align:top}} th{{position:sticky;top:74px;background:#f6fbf1;z-index:1}} pre{{margin:0;white-space:pre-wrap;font-family:inherit}}
.tools{{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}} .muted{{color:#91a08c;font-size:13px}}
@media(max-width:900px){{.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}} main{{padding:12px}}}}
</style>
</head>
<body>
<header>
<h1>EatFit Admin</h1>
<div class="muted">Версия: {escape(APP_VERSION)} · Обновлено: {escape(data['generated_at'])}</div>
<div class="tools"><a href="/admin/data?token={token}">JSON выгрузка</a><a href="/version">Версия сервера</a></div>
</header>
<main>
<div class="stats">
<div class="stat"><b>{summary['customers']}</b><span>покупателей всего</span></div>
<div class="stat"><b>{summary['registered_customers']}</b><span>в бонусной программе</span></div>
<div class="stat"><b>{summary['orders']}</b><span>заказов всего</span></div>
<div class="stat"><b>{summary['paid_or_done_orders']}</b><span>оплачено/доставлено</span></div>
<div class="stat"><b>{summary['total_orders_value']:,}</b><span>VND сумма заказов</span></div>
<div class="stat"><b>{summary['total_paid_value']:,}</b><span>VND оплачено</span></div>
</div>
<section>
<h2>Покупатели</h2>
<table><thead><tr><th>Имя</th><th>Телефон</th><th>Бонусы</th><th>Уровень</th><th>XP</th><th>Баланс</th><th>Заказов</th><th>Оплачено</th><th>Сумма</th><th>Статусы</th><th>Последний заказ</th></tr></thead><tbody>{''.join(customer_rows)}</tbody></table>
</section>
<section>
<h2>Все заказы</h2>
<table><thead><tr><th>Дата</th><th>№</th><th>Статус</th><th>Клиент</th><th>Телефон</th><th>Связь</th><th>Сумма</th><th>К оплате</th><th>Бонусы</th><th>Списано/начисл.</th><th>XP</th><th>Адрес</th><th>Состав</th></tr></thead><tbody>{''.join(order_rows)}</tbody></table>
</section>
</main>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def telegram_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return web.Response(text="ok")
    except Exception as e:
        print("TELEGRAM WEBHOOK ERROR:", e)
        return web.Response(text="webhook error", status=500)


def create_web_app():
    app_web = web.Application()
    app_web.router.add_route("*", "/site-order", site_order)
    app_web.router.add_route("*", "/loyalty-register", loyalty_register)
    app_web.router.add_route("*", "/loyalty-status", loyalty_status)
    app_web.router.add_route("*", "/loyalty-orders", loyalty_orders)
    app_web.router.add_route("*", "/order-status", order_status_web)
    app_web.router.add_route("*", "/telegram-webhook", telegram_webhook)
    app_web.router.add_route("*", "/admin/data", admin_data)
    app_web.router.add_get("/admin", admin_dashboard)
    app_web.router.add_get("/version", version)
    app_web.router.add_get("/test", test)
    return app_web


def create_bot_application():
    global telegram_app

    app = Application.builder().token(TOKEN).build()
    telegram_app = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^(🍽 Меню|🛍 Корзина|📦 Мои заказы|📊 Рассчитать КБЖУ|💬 Связаться с менеджером)$"), main_menu_buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(catalog_callback, pattern="^catalog_"))
    app.add_handler(CallbackQueryHandler(add_to_cart_callback, pattern="^add_"))
    app.add_handler(CallbackQueryHandler(cart_button_callback, pattern="^cart$"))
    app.add_handler(CallbackQueryHandler(plus_callback, pattern="^plus_"))
    app.add_handler(CallbackQueryHandler(minus_callback, pattern="^minus_"))
    app.add_handler(CallbackQueryHandler(clear_cart_callback, pattern="^clear_cart$"))
    app.add_handler(CallbackQueryHandler(checkout_callback, pattern="^checkout$"))
    app.add_handler(CallbackQueryHandler(confirm_order_callback, pattern="^confirm_order$"))
    app.add_handler(CallbackQueryHandler(status_callback, pattern="^(status_|orderstatus_)"))
    app.add_handler(CallbackQueryHandler(cancel_order_callback, pattern="^cancel_order$"))

    return app


async def run_webhook_server(app):
    app_web = create_web_app()

    await app.initialize()
    await app.start()

    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 10000)
    await site.start()

    webhook_url = f"{PUBLIC_BASE_URL}/telegram-webhook"
    await app.bot.set_webhook(
        webhook_url,
        allowed_updates=["message", "callback_query"],
    )
    print(f"WEBHOOK SET: {webhook_url}")

    while True:
        await asyncio.sleep(3600)


def start_web_server():
    async def runner():
        app_web = create_web_app()
        runner_app = web.AppRunner(app_web)
        await runner_app.setup()
        site = web.TCPSite(runner_app, "0.0.0.0", 10000)
        await site.start()

        while True:
            await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(runner())


def main():
    load_orders()
    load_users()
    load_admin_tokens()

    app = create_bot_application()

    if os.getenv("BOT_MODE", "webhook").lower() == "polling":
        threading.Thread(target=start_web_server, daemon=True).start()
        app.run_polling()
        return

    asyncio.run(run_webhook_server(app))


if __name__ == "__main__":
    main()
