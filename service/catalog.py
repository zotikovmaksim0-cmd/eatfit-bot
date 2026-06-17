import json
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

DATA_FILE = Path("data/products.json")


def get_products():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def dishes_text(count):
    if 11 <= count % 100 <= 14:
        return "блюд"

    last = count % 10

    if last == 1:
        return "блюдо"

    if last in (2, 3, 4):
        return "блюда"

    return "блюд"


def build_caption(product):
    return (
        f"🍽 {product['name']}\n\n"
        f"{product['description']}\n\n"
        f"💰 {product['price']:,} VND\n\n"
        f"🥩 Белки: {product['protein']} г\n"
        f"🥑 Жиры: {product['fat']} г\n"
        f"🍚 Углеводы: {product['carbs']} г\n"
        f"🔥 Калории: {product['calories']} ккал"
    )


def build_keyboard(index, total, cart_count=0):
    nav_buttons = []

    if index > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️", callback_data=f"catalog_{index - 1}")
        )

    nav_buttons.append(
        InlineKeyboardButton(f"{index + 1}/{total}", callback_data="ignore")
    )

    if index < total - 1:
        nav_buttons.append(
            InlineKeyboardButton("➡️", callback_data=f"catalog_{index + 1}")
        )

    return InlineKeyboardMarkup([
        nav_buttons,
        [InlineKeyboardButton("🛒 Добавить в корзину", callback_data=f"add_{index}")],
        [InlineKeyboardButton(f"🛍 {cart_count} {dishes_text(cart_count)}" if cart_count > 0 else "🛍 Корзина", callback_data="cart")],
        [InlineKeyboardButton(
            "💬 Связаться с менеджером",
            url="https://t.me/max_zoti_kov"
        )]
    ])


async def show_product(query, context, index):
    products = get_products()
    product = products[index]
    caption = build_caption(product)
    photo = f"assets/{product['images'][0]}"

    from telegram import InputMediaPhoto

    try:
        with open(photo, "rb") as image:
            media = InputMediaPhoto(media=image, caption=caption)

            await query.edit_message_media(
                media=media,
                reply_markup=build_keyboard(index, len(products), 0)
            )

    except Exception:
        with open(photo, "rb") as image:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=image,
                caption=caption,
                reply_markup=build_keyboard(index, len(products), 0)
            )


async def show_catalog(query, context):
    products = get_products()

    if not products:
        await query.message.reply_text("Каталог пуст.")
        return

    product = products[0]
    caption = build_caption(product)
    photo = f"assets/{product['images'][0]}"

    with open(photo, "rb") as image:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=image,
            caption=caption,
            reply_markup=build_keyboard(0, len(products), 0)
        )
