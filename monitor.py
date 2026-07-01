import asyncio
import json
import os
import sys
import traceback
import requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "stock_state.json"

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
            
            # 更健壮的导航策略
            try:
                await page.goto(product["url"], wait_until="domcontentloaded", timeout=45000)
            except PlaywrightTimeout:
                print(f"  ⚠️ domcontentloaded timeout, trying without wait...")
                await page.goto(product["url"], timeout=45000)
            
            # 额外等待 JS 渲染
            await page.wait_for_timeout(4000)
            
            # 尝试等待关键元素（可选）
            try:
                await page.wait_for_selector(
                    'button:has-text("Add to Cart"), [class*="add-to"], text=/EU|UK|42/i',
                    timeout=8000
                )
            except:
                pass

            # 精准检测逻辑（保持之前优化版）
            is_size_selectable = False
            target_texts = ["EU 42", "42", "UK 8", "UK8"]

            for target in target_texts:
                try:
                    locator = page.locator(
                        f'button:has-text("{target}"), option:has-text("{target}"), '
                        f'[class*="swatch"]:has-text("{target}"), [class*="option"]:has-text("{target}"), '
                        f'li:has-text("{target}"), span:has-text("{target}")'
                    )
                    count = await locator.count()
                    for i in range(min(count, 8)):
                        el = locator.nth(i)
                        try:
                            cls = (await el.get_attribute("class") or "").lower()
                            if any(bad in cls for bad in ["disabled", "unavailable", "out-of-stock", "sold-out"]):
                                continue
                            try:
                                if await el.is_disabled():
                                    continue
                            except:
                                pass
                            txt = (await el.text_content() or "").lower()
                            if any(bad in txt for bad in ["out of stock", "notify me", "sold out"]):
                                continue
                            is_size_selectable = True
                            print(f"  ✅ Found available size: {target}")
                            break
                        except:
                            continue
                    if is_size_selectable:
                        break
                except Exception as loc_e:
                    print(f"  Locator warning for {target}: {loc_e}")

            content = (await page.content()).lower()
            has_target = any(t.lower() in content for t in ["eu 42", "42", "uk 8"])
            has_add = "add to cart" in content
            has_out = "out of stock" in content or "sold out" in content
            has_notify = "notify me" in content

            available = is_size_selectable or (has_target and has_add and not has_out and not has_notify)
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
