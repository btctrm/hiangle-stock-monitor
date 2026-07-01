import asyncio
import json
import os
import requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright

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
        except Exception:
            return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

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
        if resp.status_code != 200:
            print(f"Telegram send failed: {resp.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

async def check_product(product):
    """返回 True 表示目标尺码 (EU 42 / UK 8) 当前可购买"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-GB"
        )
        page = await context.new_page()

        try:
            print(f"🔍 Checking {product['name']} ...")
            await page.goto(product["url"], wait_until="networkidle", timeout=60000)

            # 等待关键元素（尺寸选择器或 Add to Cart）
            try:
                await page.wait_for_selector(
                    'button:has-text("Add to Cart"), [class*="add-to"], select, [class*="swatch"], [class*="size-option"], text=/EU|UK|42/i',
                    timeout=12000
                )
            except:
                pass

            await page.wait_for_timeout(3500)  # 额外等待 JS 渲染

            # === 精准检测：locator 分析尺寸元素 ===
            is_size_selectable = False
            target_texts = ["EU 42", "42", "UK 8", "UK8"]

            for target in target_texts:
                try:
                    locator = page.locator(
                        f'button:has-text("{target}"), '
                        f'option:has-text("{target}"), '
                        f'[class*="swatch"]:has-text("{target}"), '
                        f'[class*="option"]:has-text("{target}"), '
                        f'li:has-text("{target}"), '
                        f'span:has-text("{target}"), '
                        f'[data-size*="{target}"]'
                    )
                    count = await locator.count()
                    for i in range(min(count, 8)):
                        el = locator.nth(i)
                        try:
                            cls = (await el.get_attribute("class") or "").lower()
                            txt = (await el.text_content() or "").lower()

                            # 检查禁用状态
                            try:
                                if await el.is_disabled():
                                    continue
                            except:
                                pass
                            if any(bad in cls for bad in ["disabled", "unavailable", "out-of-stock", "sold-out", "notify"]):
                                continue

                            # 检查附近文本是否有缺货提示
                            context_text = txt
                            try:
                                parent = el.locator("xpath=ancestor-or-self::*[position()<=4]")
                                context_text += " " + (await parent.text_content() or "").lower()
                            except:
                                pass

                            if any(bad in context_text for bad in ["out of stock", "notify me", "email when", "sold out", "unavailable", "stock: 0"]):
                                continue

                            # 成功找到可用尺寸元素
                            is_size_selectable = True
                            print(f"  ✅ Found selectable size element: {target} | text≈{txt[:60]}")
                            break
                        except:
                            continue
                    if is_size_selectable:
                        break
                except Exception as loc_err:
                    print(f"  Locator error for {target}: {loc_err}")

            # === 回退检测（兼容不同站点结构）===
            content = (await page.content()).lower()
            has_target_size = any(t.lower() in content for t in ["eu 42", "42", "uk 8", "uk8"])
            has_add_to_cart = "add to cart" in content or "add to bag" in content
            has_out = "out of stock" in content or "sold out" in content
            has_notify = "notify me" in content or "email when available" in content

            available = is_size_selectable or (has_target_size and has_add_to_cart and not has_out and not has_notify)

            print(f"  📊 Result: selectable={is_size_selectable}, has_size={has_target_size}, "
                  f"add_to_cart={has_add_to_cart}, out={has_out}, notify={has_notify} → available={available}")

            return available

        except Exception as e:
            print(f"❌ Error checking {product['name']}: {str(e)[:300]}")
            return False
        finally:
            await browser.close()

async def main():
    state = load_state()
    notified = False
    now_iso = datetime.now(timezone.utc).isoformat()

    for product in PRODUCTS:
        key = product["name"]
        prev = state.get(key, {})
        prev_available = prev.get("last_available", False)

        current_available = await check_product(product)

        # 更新状态
        state[key] = {
            "name": product["name"],
            "last_available": current_available,
            "last_checked": now_iso,
            "url": product["url"]
        }

        if current_available and not prev_available:
            # 仅在“新到货”时通知
            message = (
                f"🎉 **BananaFingers 库存警报！**\n\n"
                f"**{product['name']}**\n"
                f"✅ **EU 42 / UK 8 现已可购买**\n\n"
                f"🔗 {product['url']}\n\n"
                f"请尽快前往确认下单！\n"
                f"_检测时间: {now_iso}_"
            )
            send_telegram(message)
            print(f"🔔 已发送通知: {product['name']}")
            notified = True
        elif current_available:
            print(f"ℹ️ {product['name']} 仍可购买（未重复通知）")
        else:
            print(f"❌ {product['name']} 目标尺码暂无货")

    save_state(state)
    print(f"\n✅ 本次检测完成 | 新通知: {notified} | 状态已保存到 {STATE_FILE}")

if __name__ == "__main__":
    asyncio.run(main())
