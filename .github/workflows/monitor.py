import asyncio
import os
import requests
from playwright.async_api import async_playwright

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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

async def check_product(product):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(product["url"], wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(2500)  # 等 JS 加载尺寸

            content = await page.content()
            content_lower = content.lower()

            # 检测 EU 42 或 UK 8 是否可用
            size_found = False
            for size in ["eu 42", "42", "uk 8", "uk8"]:
                if size in content_lower:
                    size_found = True
                    break

            if size_found:
                if "add to cart" in content_lower and "out of stock" not in content_lower and "notify me" not in content_lower:
                    message = (
                        f"🎉 {product['name']} EU 42 / UK 8 可能有货！\n\n"
                        f"{product['url']}\n\n"
                        "请尽快去确认下单！"
                    )
                    send_telegram(message)
                    print(f"ALERT SENT for {product['name']}")
                    return True
        except Exception as e:
            print(f"Error checking {product['name']}: {e}")
        finally:
            await browser.close()
    return False

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram env vars")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    except Exception as e:
        print(f"Telegram error: {e}")

async def main():
    for product in PRODUCTS:
        await check_product(product)

if __name__ == "__main__":
    asyncio.run(main())
