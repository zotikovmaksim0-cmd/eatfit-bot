from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, KeyboardButton, ReplyKeyboardMarkup
from datetime import datetime
import json
from pathlib import Path

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
            ["💬 Связаться с менеджером"]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


TOKEN = "8447362025:AAGk2pNyIHeogcQjWvFqVsP86DLa8ovMHSM"

carts = {}

order_data = {}

orders = {}

ORDER_CHAT_ID = 619240147

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
    except Exception:
        pass

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


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

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

    elif txt == "💬 Связаться с менеджером":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Открыть чат с менеджером", url="https://t.me/max_zoti_kov")]
        ])
        await update.message.reply_text(
            "Связь с менеджером:",
            reply_markup=keyboard
        )


def main():
    load_orders()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(MessageHandler(filters.Regex(r"^(🍽 Меню|🛍 Корзина|📦 Мои заказы|💬 Связаться с менеджером)$"), main_menu_buttons))
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

    app.run_polling()


import asyncio

if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
