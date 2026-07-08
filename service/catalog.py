import json
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


PRODUCTS_FILE = Path("data/products.json")


def format_vnd(value):
    return f"{int(value):,} VND"


def get_products():
    if not PRODUCTS_FILE.exists():
        return []

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def build_caption(product):
    return (
        f"🥗 {product['name']}\n\n"
        f"{product.get('description', '')}\n\n"
        f"🥩 Белки: {product.get('protein', 0)} г\n"
        f"🥑 Жиры: {product.get('fat', 0)} г\n"
        f"🍚 Углеводы: {product.get('carbs', 0)} г\n"
        f"🔥 Калории: {product.get('calories', 0)} ккал\n\n"
        f"💰 {format_vnd(product.get('price', 0))}"
    )


def build_keyboard(index, total, cart_count=0):
    prev_index = (index - 1) % total if total else 0
    next_index = (index + 1) % total if total else 0

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️", callback_data=f"catalog_{prev_index}"),
                InlineKeyboardButton("➡️", callback_data=f"catalog_{next_index}"),
            ],
            [InlineKeyboardButton("➕ В корзину", callback_data=f"add_{index}")],
            [InlineKeyboardButton(f"🛍 Корзина ({cart_count})", callback_data="cart")],
        ]
    )


async def show_catalog(query, context):
    products = get_products()
    if not products:
        await query.message.reply_text("Меню пока недоступно.")
        return

    keyboard = [
        [InlineKeyboardButton(product["name"], callback_data=f"catalog_{index}")]
        for index, product in enumerate(products)
    ]

    text = "🍽 Меню EatFit\n\nВыберите блюдо:"
    markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_text(text, reply_markup=markup)
    except Exception:
        await query.message.reply_text(text, reply_markup=markup)


async def show_product(query, context, index):
    products = get_products()
    if not products:
        await query.message.reply_text("Меню пока недоступно.")
        return

    safe_index = max(0, min(index, len(products) - 1))
    product = products[safe_index]
    caption = build_caption(product)
    keyboard = build_keyboard(safe_index, len(products), 0)
    image_path = Path("assets") / product["images"][0]

    try:
        with open(image_path, "rb") as image:
            await query.message.reply_photo(
                photo=image,
                caption=caption,
                reply_markup=keyboard,
            )
    except Exception:
        await query.message.reply_text(caption, reply_markup=keyboard)
