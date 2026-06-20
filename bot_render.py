# KBJU REWORK VERSION
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, KeyboardButton, ReplyKeyboardMarkup
from datetime import datetime
import json
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

ORDER_CHAT_ID = 619240147

telegram_app = None

ORDERS_FILE = Path("orders.json")

def save_orders():
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)

def load_orders():
    global orders

    if ORDERS_FILE.exists():
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            orders = json.load(f)



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
    status_map = {
        "new": "🟡 Новый",
        "confirmed": "🔵 Подтвержден",
        "preparing": "🟠 Готовится",
        "delivery": "🟣 В доставке",
        "done": "🟢 Доставлен"
    }

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
        f"🟠 Готовится: {stats['preparing']}\n"
        f"🟣 В доставке: {stats['delivery']}\n"
        f"🟢 Доставлено: {stats['done']}\n\n"
        f"💰 Общая сумма:\n{revenue:,} VND"
    )

    await update.message.reply_text(message)


async def cart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, keyboard = get_cart_text_and_keyboard(update.effective_user.id)

    await update.message.reply_text(text, reply_markup=keyboard)




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

    status_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 Подтвердить", callback_data=f"status_confirmed|{order_number}")],
        [InlineKeyboardButton("🟠 Готовится", callback_data=f"status_preparing|{order_number}")],
        [InlineKeyboardButton("🟣 В доставке", callback_data=f"status_delivery|{order_number}")],
        [InlineKeyboardButton("🟢 Доставлен", callback_data=f"status_done|{order_number}")]
    ])

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
    await query.answer()

    data = query.data.split("|")
    status = data[0].replace("status_", "")
    order_number = data[1]

    if order_number not in orders:
        return

    user_id = orders[order_number]["user_id"]
    orders[order_number]["status"] = status
    save_orders()

    status_map = {
        "confirmed": "🔵 Подтвержден",
        "preparing": "🟠 Готовится",
        "delivery": "🟣 В доставке",
        "done": "🟢 Доставлен"
    }

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"📦 Обновление заказа\n\n"
            f"Заказ: {order_number}\n\n"
            f"Статус: {status_map[status]}"
        )
    )

    try:
        updated_text = (
            orders[order_number]["order_text"]
            + f"\n\n📌 Статус:\n{status_map[status]}"
        )

        await context.bot.edit_message_text(
            chat_id=ORDER_CHAT_ID,
            message_id=orders[order_number]["manager_message_id"],
            text=updated_text,
            reply_markup=query.message.reply_markup
        )
    except Exception as e:
        print("STATUS UPDATE ERROR:", e)

    await query.answer("Статус обновлен")


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
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            }
        )

    try:
        data = await request.json()

        print("SITE ORDER RECEIVED")
        print(data)

        text_order = (
            f"🔔 Новый заказ с сайта\n\n"
            f"№ {data.get('order_id','')}\n\n"
            f"👤 {data.get('name','')}\n\n"
            f"📞 {data.get('phone','')}\n\n"
            f"🏠 {data.get('address','')}\n\n"
            f"🛒 Заказ:\n\n"
            f"{data.get('items','')}\n\n"
            f"💰 Итого: {data.get('total','')} VND"
        )

        await telegram_app.bot.send_message(
            chat_id=ORDER_CHAT_ID,
            text=text_order
        )

        print("SITE ORDER SENT TO TELEGRAM")

        response = web.json_response({"success": True})
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response
    except Exception as e:
        print("TELEGRAM ERROR:", e)
        return web.json_response({"success": False, "error": str(e)})



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

    global telegram_app

    app = Application.builder().token(TOKEN).build()
    telegram_app = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
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
