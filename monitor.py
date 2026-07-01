import asyncio
import json
import os
import re
import sys
import traceback
import requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "stock_state.json"

# 目标尺码：UK 8 / EU 42
TARGET_UK = 8.0
TARGET_EU = 42.0

PRODUCTS = [
    {
        "url": "https://bananafingers.com/catalog/product/view/id/101942/s/five-ten-hiangle/category/3388/",
        "name": "Five Ten Hiangle (Men's)"
    },
    {
        "url": "https://bananafingers.com/five-ten-hiangle-women-s",
        "name": "Five Ten Hiangle (Women's)"
    }
]

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: load_state failed: {e}")
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: save_state failed: {e}")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        print(f"Telegram response: {resp.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

async def check_product(product):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()
        try:
            print(f"🔍 Checking {product['name']} ...")

            try:
                await page.goto(product["url"], wait_until="domcontentloaded", timeout=45000)
            except PlaywrightTimeout:
                print("  ⚠️ domcontentloaded timeout, trying without wait...")
                await page.goto(product["url"], timeout=45000)

            # BananaFingers (Magento) 把尺码放在 <select class="super-attribute-select">
            # 里，每个 <option> 缺货时会被打上 disabled 属性——这是判断库存唯一可靠的信号，
            # 不能靠在整页 HTML 里搜索裸数字 "42"（价格、其他尺码换算比如 42.66 EU 都会
            # 包含 "42"，导致误判）。
            try:
                await page.wait_for_selector("select.super-attribute-select", timeout=15000)
            except PlaywrightTimeout:
                print("  ⚠️ 未找到尺码选择框（select.super-attribute-select），页面结构可能变了")
                return False

            select = page.locator("select.super-attribute-select").first
            options = select.locator("option")
            count = await options.count()

            available = False
            found_target = False

            for i in range(count):
                opt = options.nth(i)
                text = (await opt.text_content() or "").strip()
                # 例如 "8 UK / 42 EU"，用带单位的数字对精确匹配，避免 "8.5 UK / 42.66 EU" 之类误命中
                m = re.search(r"(\d+(?:\.\d+)?)\s*UK\s*/\s*(\d+(?:\.\d+)?)\s*EU", text, re.I)
                if not m:
                    continue
                uk_size = float(m.group(1))
                eu_size = float(m.group(2))
                if abs(uk_size - TARGET_UK) < 0.01 and abs(eu_size - TARGET_EU) < 0.01:
                    found_target = True
                    is_disabled = await opt.is_disabled()
                    available = not is_disabled
                    status = "有货" if available else "缺货"
                    print(f"  {'✅' if available else '⛔'} 目标尺码 \"{text}\" → {status} (disabled={is_disabled})")
                    break

            if not found_target:
                print(f"  ⚠️ 尺码列表中没有找到 UK{TARGET_UK}/EU{TARGET_EU}，判定为缺货")

            print(f"  Result → available={available}")
            return available

        except Exception as e:
            print(f"❌ Error checking {product['name']}: {str(e)[:200]}")
            return False
        finally:
            await browser.close()

async def main():
    try:
        print("=== Stock Monitor Started ===")
        print(f"Python version: {sys.version.split()[0]}")
        state = load_state()
        notified = False
        now_iso = datetime.now(timezone.utc).isoformat()

        for product in PRODUCTS:
            key = product["name"]
            prev_available = state.get(key, {}).get("last_available", False)
            current_available = await check_product(product)

            state[key] = {
                "name": product["name"],
                "last_available": current_available,
                "last_checked": now_iso,
                "url": product["url"]
            }

            if current_available and not prev_available:
                message = (
                    f"🎉 BananaFingers 库存警报！\n\n"
                    f"**{product['name']}**\n"
                    f"✅ EU 42 / UK 8 现已可购买\n\n"
                    f"🔗 {product['url']}\n\n"
                    f"请尽快确认下单！\n检测时间: {now_iso}"
                )
                send_telegram(message)
                notified = True
                print(f"🔔 已发送通知: {product['name']}")
            elif current_available:
                print(f"ℹ️ {product['name']} 仍可购买（未重复通知）")
            else:
                print(f"❌ {product['name']} 暂无货")

        save_state(state)
        print(f"=== 检测完成 | 新通知: {notified} ===")

    except Exception as e:
        print("=== FATAL ERROR ===")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
