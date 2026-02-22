#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import io
import time
import threading
from datetime import datetime

import requests
import matplotlib
matplotlib.use('Agg')  # обязательно для сервера без GUI
import matplotlib.pyplot as plt

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, jsonify

# ===== ТВОЙ ТОКЕН =====
TOKEN = "8403715390:AAEdo8Tbl6Ns70X27CbLGBxjg5S_u3ctwzY"
# ======================

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Страны для проверки
LOCATIONS = [
    {"country": "RU", "name": "🇷🇺 Россия"},
    {"country": "US", "name": "🇺🇸 США"},
    {"country": "DE", "name": "🇩🇪 Германия"},
    {"country": "JP", "name": "🇯🇵 Япония"},
    {"country": "BR", "name": "🇧🇷 Бразилия"},
    {"country": "AU", "name": "🇦🇺 Австралия"},
]

# ---------- Flask для health check (всегда отвечает 200) ----------
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({"status": "alive", "message": "Telegram bot is running"})

@flask_app.route('/health')
def health():
    return jsonify({"status": "ok"})

# Ловим любые пути, которые запрашивает платформа (например /kaithhealthcheck)
@flask_app.route('/<path:path>')
def catch_all(path):
    return jsonify({"status": "ok", "path": path}), 200

# ---------- Функции бота (без изменений, все рабочие) ----------
async def check_site_global(domain: str):
    results = []
    for loc in LOCATIONS:
        payload = {
            "type": "http",
            "target": domain,
            "locations": [{"country": loc["country"]}],
            "measurementOptions": {
                "protocol": "HTTPS",
                "port": 443,
                "request": {"path": "/", "method": "HEAD"},
            },
        }
        try:
            resp = requests.post(
                "https://api.globalping.io/v1/measurements",
                json=payload,
                timeout=15,
            )
            if resp.status_code != 202:
                results.append({
                    "country": loc["country"],
                    "status": "⚠️ Ошибка создания",
                    "response_time": 0,
                    "error": f"HTTP {resp.status_code}",
                })
                continue
            data = resp.json()
            measurement_id = data["id"]
            time.sleep(3)
            result_resp = requests.get(
                f"https://api.globalping.io/v1/measurements/{measurement_id}",
                timeout=10,
            )
            if result_resp.status_code != 200:
                results.append({
                    "country": loc["country"],
                    "status": "⚠️ Нет результатов",
                    "response_time": 0,
                    "error": f"HTTP {result_resp.status_code}",
                })
                continue
            result_data = result_resp.json()
            if "results" in result_data and len(result_data["results"]) > 0:
                probe_result = result_data["results"][0]
                status = "✅ Доступен" if probe_result.get("status") == "finished" else "❌ Недоступен"
                timings = probe_result.get("timings", {})
                response_time = timings.get("total", 0)
                results.append({
                    "country": loc["country"],
                    "status": status,
                    "response_time": response_time,
                    "error": probe_result.get("error"),
                })
            else:
                results.append({
                    "country": loc["country"],
                    "status": "⚠️ Нет данных",
                    "response_time": 0,
                    "error": "Пустой ответ",
                })
        except Exception as e:
            logger.error(f"Ошибка при проверке {loc['country']}: {e}")
            results.append({
                "country": loc["country"],
                "status": "⚠️ Ошибка",
                "response_time": 0,
                "error": str(e)[:50],
            })
    return results

def create_status_chart(results, domain):
    countries = []
    status_colors = []
    response_times = []
    country_names = {loc["country"]: loc["name"] for loc in LOCATIONS}
    for r in results:
        country = country_names.get(r["country"], r["country"])
        countries.append(country)
        response_times.append(r["response_time"] / 1000)
        if "✅" in r["status"]:
            status_colors.append("green")
        elif "❌" in r["status"]:
            status_colors.append("red")
        else:
            status_colors.append("orange")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
    fig.suptitle(f"Доступность сайта {domain}", fontsize=14)
    ax1.bar(countries, [1] * len(countries), color=status_colors, alpha=0.7)
    ax1.set_ylim(0, 1.5)
    ax1.set_ylabel("Статус")
    ax1.set_title("Зелёный — доступен, Красный — недоступен, Оранжевый — ошибка")
    ax1.tick_params(axis="x", rotation=45)
    ax1.set_yticks([])
    bars = ax2.bar(countries, response_times, color="blue", alpha=0.6)
    ax2.set_ylabel("Время отклика (сек)")
    ax2.set_title("Время загрузки (только для доступных)")
    ax2.tick_params(axis="x", rotation=45)
    for bar, t in zip(bars, response_times):
        if t > 0:
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.05,
                f"{t:.2f}с",
                ha="center", va="bottom", fontsize=9
            )
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=100)
    buf.seek(0)
    plt.close(fig)
    return buf

def analyze_blocking(results):
    ru_result = None
    other_results = []
    for r in results:
        if r["country"] == "RU":
            ru_result = r
        else:
            other_results.append(r)
    if not ru_result:
        return "❌ Не удалось получить данные по России"
    ru_available = "✅" in ru_result["status"]
    other_available = any("✅" in r["status"] for r in other_results)
    if not ru_available and other_available:
        working = [r["country"] for r in other_results if "✅" in r["status"]]
        country_names = {loc["country"]: loc["name"] for loc in LOCATIONS}
        working_names = [country_names.get(c, c) for c in working]
        return (
            f"⚠️ **ВЕРОЯТНАЯ БЛОКИРОВКА В РОССИИ**\n"
            f"Сайт доступен в: {', '.join(working_names)}"
        )
    elif not ru_available and not other_available:
        return "🌍 **ГЛОБАЛЬНАЯ ПРОБЛЕМА**\nСайт недоступен во всех проверенных странах"
    elif ru_available and not other_available:
        return (
            "⚠️ **СТРАННАЯ СИТУАЦИЯ**\n"
            "Сайт работает в России, но не работает в других странах"
        )
    else:
        return "✅ **ВСЁ ХОРОШО**\nСайт доступен во всех проверенных регионах"

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Укажите домен. Например:\n/check example.com\n/check google.ru"
        )
        return
    domain = context.args[0].lower().strip()
    domain = domain.replace("https://", "").replace("http://", "").split("/")[0]
    status_msg = await update.message.reply_text(
        f"🔍 Проверяю {domain}... Это займёт около 30 секунд"
    )
    try:
        results = await check_site_global(domain)
        analysis = analyze_blocking(results)
        chart_buf = create_status_chart(results, domain)
        country_names = {loc["country"]: loc["name"] for loc in LOCATIONS}
        text = f"📊 **Результаты проверки {domain}**\n\n"
        for r in results:
            name = country_names.get(r["country"], r["country"])
            time_str = f"{r['response_time']/1000:.2f}с" if r["response_time"] > 0 else "—"
            text += f"{name}: {r['status']} ({time_str})\n"
        text += f"\n{analysis}"
        text += f"\n\n🕒 Проверка: {datetime.now().strftime('%H:%M:%S')}"
        await status_msg.delete()
        await update.message.reply_photo(
            photo=chart_buf,
            caption=text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Ошибка в check_command")
        await status_msg.edit_text(f"❌ Ошибка при проверке: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для проверки доступности сайтов.\n\n"
        "/check <домен> — например, /check google.com"
    )

def run_bot():
    """Запуск Telegram бота в отдельном потоке"""
    try:
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("check", check_command))
        logger.info("Бот запущен и слушает команды...")
        app.run_polling()
    except Exception as e:
        logger.exception("Бот упал с ошибкой, но Flask продолжает работать")

def main():
    # Запускаем бота в фоновом потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Запускаем Flask в главном потоке (именно он держит процесс живым)
    logger.info("Запуск Flask на порту 8080 для health checks")
    flask_app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
