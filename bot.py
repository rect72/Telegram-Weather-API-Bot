import asyncio
import logging
import os
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()


class ApiError(Exception):
    """Ошибка при работе с внешним API."""


WEATHER_CODES = {
    0: "Ясно",
    1: "Преимущественно ясно",
    2: "Переменная облачность",
    3: "Пасмурно",
    45: "Туман",
    48: "Изморозь",
    51: "Слабая морось",
    53: "Морось",
    55: "Сильная морось",
    61: "Слабый дождь",
    63: "Дождь",
    65: "Сильный дождь",
    71: "Слабый снег",
    73: "Снег",
    75: "Сильный снег",
    80: "Слабые ливни",
    81: "Ливни",
    82: "Сильные ливни",
    95: "Гроза",
    96: "Гроза с градом",
    99: "Сильная гроза с градом",
}


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        async with session.get(url, params=params) as response:
            if response.status >= 400:
                try:
                    error_data = await response.json()
                    reason = error_data.get("reason") or error_data.get("message")
                except Exception:
                    reason = None

                raise ApiError(
                    reason or f"Ошибка внешнего API: HTTP {response.status}"
                )

            try:
                data = await response.json()
            except (aiohttp.ContentTypeError, ValueError) as exc:
                raise ApiError("API вернул некорректный JSON.") from exc

            if not isinstance(data, dict):
                raise ApiError("API вернул неожиданный формат данных.")

            return data

    except asyncio.TimeoutError as exc:
        raise ApiError("API отвечает слишком долго. Попробуйте ещё раз.") from exc
    except aiohttp.ClientError as exc:
        raise ApiError("Не удалось подключиться к API.") from exc


async def find_city(
    session: aiohttp.ClientSession,
    city: str,
) -> dict[str, Any]:
    data = await get_json(
        session,
        GEOCODING_URL,
        {
            "name": city,
            "count": 1,
            "language": "ru",
            "format": "json",
        },
    )

    results = data.get("results")

    if not isinstance(results, list) or not results:
        raise ApiError("Город не найден. Проверьте название.")

    location = results[0]

    if "latitude" not in location or "longitude" not in location:
        raise ApiError("API не вернул координаты города.")

    return location


async def get_weather(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
) -> dict[str, Any]:
    data = await get_json(
        session,
        WEATHER_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,relative_humidity_2m,"
                "apparent_temperature,weather_code,wind_speed_10m"
            ),
            "timezone": "auto",
        },
    )

    current = data.get("current")

    if not isinstance(current, dict):
        raise ApiError("API не вернул текущую погоду.")

    return current


def format_weather(
    location: dict[str, Any],
    current: dict[str, Any],
) -> str:
    temperature = current.get("temperature_2m")

    if temperature is None:
        raise ApiError("В JSON отсутствует температура.")

    code = current.get("weather_code")
    description = WEATHER_CODES.get(code, f"Код погоды: {code}")

    city = location.get("name", "Неизвестный город")
    country = location.get("country", "")
    place = f"{city}, {country}" if country else city

    apparent = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")

    return (
        f"Погода: {place}\n\n"
        f"Состояние: {description}\n"
        f"Температура: {temperature} °C\n"
        f"Ощущается как: "
        f"{apparent if apparent is not None else 'Нет данных'} °C\n"
        f"Влажность: "
        f"{humidity if humidity is not None else 'Нет данных'} %\n"
        f"Ветер: {wind if wind is not None else 'Нет данных'} км/ч"
    )


async def load_weather(city: str) -> str:
    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        location = await find_city(session, city)

        current = await get_weather(
            session,
            float(location["latitude"]),
            float(location["longitude"]),
        )

        return format_weather(location, current)


@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "Здравствуйте!\n\n"
        "Напишите название города, а я получу текущую погоду "
        "из внешнего API.\n"
        "Например: Москва"
    )


@dp.message()
async def city_handler(message: Message) -> None:
    city = (message.text or "").strip()

    if len(city) < 2:
        await message.answer("Введите название города текстом.")
        return

    status = await message.answer("Получаю данные из API...")

    try:
        result = await load_weather(city)
    except ApiError as exc:
        await status.edit_text(f"Не удалось получить данные.\n\n{exc}")
        return
    except Exception:
        logging.exception("Неожиданная ошибка")
        await status.edit_text(
            "Произошла внутренняя ошибка. Попробуйте ещё раз позже."
        )
        return

    await status.edit_text(result)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден. Создайте файл .env и добавьте токен бота."
        )

    bot = Bot(token=BOT_TOKEN)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
