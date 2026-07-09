# KBJU REWORK VERSION
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, KeyboardButton, ReplyKeyboardMarkup
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
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

telegram_app = None

DATA_DIR = Path(os.getenv("EATFIT_DATA_DIR", "."))
ORDERS_FILE = DATA_DIR / "orders.json"
USERS_FILE = DATA_DIR / "users.json"
WELCOME_BONUS = 30000
WELCOME_BONUS_DAYS = 7
ORDER_BONUS_DAYS = 30
XP_PER_1000_VND = 1
FIRST_ORDER_XP = 300
FAST_FIRST_ORDER_XP = 500
COINS_PER_100000_VND = 100
STREAK_REWARDS = {
    7: {"xp": 500, "coins": 0},
    14: {"xp": 1000, "coins": 0},
    30: {"xp": 2000, "coins": 1000},
}
LEVELS = [
    {"name": "Legend", "xp": 70000, "bonus_rate": 0.10, "discount_rate": 0.10},
    {"name": "Elite", "xp": 35000, "bonus_rate": 0.07, "discount_rate": 0.07},
    {"name": "Champion", "xp": 15000, "bonus_rate": 0.05, "discount_rate": 0.05},
    {"name": "Athlete", "xp": 5000, "bonus_rate": 0.03, "discount_rate": 0},
    {"name": "Rookie", "xp": 0, "bonus_rate": 0.01, "discount_rate": 0},
]

users = {}


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
    if digits.startswith("84") and len(digits) >= 10:
        return digits
    if digits.startswith("0") and len(digits) >= 9:
        return "84" + digits[1:]
    if len(digits) == 9 and digits[0] in "35789":
        return "84" + digits
    return digits


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
    if int(user.get("orders_count", 0)) == 0:
        xp_earned += FIRST_ORDER_XP
        try:
            created_at = datetime.fromisoformat(user.get("created_at", now_iso()))
            if (datetime.utcnow() - created_at).total_seconds() <= 86400:
                xp_earned += FAST_FIRST_ORDER_XP
        except Exception:
            pass
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
    orders_before = int(user.get("orders_count", 0))
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
    if orders_before == 0:
        xp_earned += FIRST_ORDER_XP
        try:
            created_at = datetime.fromisoformat(user.get("created_at", now_iso()))
            if (datetime.utcnow() - created_at).total_seconds() <= 86400:
                xp_earned += FAST_FIRST_ORDER_XP
        except Exception:
            pass
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
        f"Начисление уровня: {int(bonus_rate * 100)}%\n"
        f"Серия заказов: {streak_days} дн.\n\n"
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


def build_status_keyboard(order_number, current_status=""):
    buttons = [
        ("confirmed", "🔵 Подтвердить"),
        ("paid", "💳 Оплачен"),
        ("preparing", "🟠 Готовится"),
        ("delivery", "🟣 В доставке"),
        ("done", "🟢 Доставлен"),
    ]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"status_{status}|{order_number}")]
        for status, label in buttons
        if status != current_status
    ])


def clean_order_message_text(text):
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
    return f"{base_text}\n\n📌 Текущий статус:\n{status_map.get(status, status)}"



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
            text=order_text,
            reply_markup=status_keyboard
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
    status = data[0].replace("status_", "")
    order_number = data[1]

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
    if status == "paid":
        apply_loyalty_payment(order_number)
    save_orders()

    status_map = order_status_map()

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
        updated_text = order_text_with_status(orders[order_number], status)

        await context.bot.edit_message_text(
            chat_id=query.message.chat_id,
            message_id=orders[order_number].get("manager_message_id") or query.message.message_id,
            text=updated_text,
            reply_markup=build_status_keyboard(order_number, status)
        )
    except Exception as e:
        print("STATUS UPDATE ERROR:", e)

    await query.answer(f"Статус: {status_map.get(status, status)}")


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
        customer_name = " ".join(
            part for part in [data.get("name", ""), data.get("surname", "")]
            if part
        )
        order_number = data.get("order_id") or datetime.now().strftime("SITE-%Y%m%d-%H%M%S")

        loyalty_phone, loyalty_user = get_user_by_phone(data.get("loyalty_phone") or data.get("phone"))
        if loyalty_phone and not loyalty_user:
            users[loyalty_phone] = {
                "name": data.get("name", ""),
                "surname": data.get("surname", ""),
                "phone": loyalty_phone,
                "contact_method": contact_method,
                "contact_value": contact_value,
                "bonus_balance": 0,
                "xp": 0,
                "coins": 0,
                "streak_days": 0,
                "orders_count": 0,
                "total_spent": 0,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "auto_registered": True,
                "welcome_bonus_granted": False,
                "bonus_entries": [],
            }
            loyalty_user = users[loyalty_phone]
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
            f"📞 {data.get('phone','')}\n\n"
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
        status_keyboard = build_status_keyboard(order_number)
        orders[order_number] = {
            "user_id": None,
            "status": "new",
            "source": "site",
            "order_text": text_order,
            "manager_message_id": None,
            "customer_name": customer_name,
            "phone": data.get("phone", ""),
            "total": final_total,
            "total_value": total_value,
            "loyalty_phone": loyalty_phone,
            "use_bonus": use_bonus,
            "loyalty_applied": False,
            "loyalty": loyalty_result,
            "created_at": now_iso(),
        }

        sent_message = await telegram_app.bot.send_message(
            chat_id=ORDER_CHAT_ID,
            text=text_order,
            reply_markup=status_keyboard
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
            return cors_response({"success": False, "error": "phone_required"}, status=400)

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
    return cors_response({
        "success": True,
        "registered": bool(user),
        "user": public_user(user) if user else None,
        "welcome_bonus": WELCOME_BONUS,
        "bonus_rate": level_bonus_rate(club_level(int(user.get("xp", 0)))) if user else level_bonus_rate(LEVELS[-1]),
    })



async def test(request):
    try:
        await telegram_app.bot.send_message(
            chat_id=ORDER_CHAT_ID,
            text="✅ TEST MESSAGE FROM RENDER"
        )
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


def start_web_server():
    async def runner():
        app_web = web.Application()
        app_web.router.add_route("*", "/site-order", site_order)
        app_web.router.add_route("*", "/loyalty-register", loyalty_register)
        app_web.router.add_route("*", "/loyalty-status", loyalty_status)
        app_web.router.add_get("/test", test)

        runner = web.AppRunner(app_web)
        await runner.setup()

        site = web.TCPSite(runner, "0.0.0.0", 10000)
        await site.start()

        while True:
            await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(runner())


def main():
    load_orders()
    load_users()

    global telegram_app

    app = Application.builder().token(TOKEN).build()
    telegram_app = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
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
    app.add_handler(CallbackQueryHandler(status_callback, pattern="^status_"))
    app.add_handler(CallbackQueryHandler(cancel_order_callback, pattern="^cancel_order$"))

    threading.Thread(target=start_web_server, daemon=True).start()

    app.run_polling()


import asyncio

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
