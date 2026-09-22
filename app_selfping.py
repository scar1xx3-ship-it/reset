import os
import sys
import io
import json
import time
import random
import threading
import shutil
import re
import urllib.parse
import uuid
import subprocess
from flask import Flask, render_template, request, jsonify, session, redirect, send_file
from instagrapi import Client
from pathlib import Path

# Ensure Windows console does not crash on unicode prints
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


import base64
import hashlib
import requests
from datetime import datetime
from PIL import Image

# ================= EMBEDDED HIGH-RESILIENCE INSTAGRAM BOT ENGINE =================

# --- Authentic Instagram Android App Constants ---
IG_APP_VERSION = "408.0.0.51.78"
IG_VERSION_CODE = "380706635"
IG_APP_ID = "567067343352427"
IG_CAPABILITIES = "3brTv10="
BLOKS_VERSION_ID = "388ece79ebc0e70e87873505ed1b0ff335ae2868a978cc951b6721c41d46a30a"
DEFAULT_CSRF_TOKEN = "U7Cig3ldICjza7ZNNRmhVnYVrioIfBiA"
WEB_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
IG_WEB_APP_ID = "936619743392459"
API_BASE_URL = "https://i.instagram.com/api/v1"
GRAPHQL_DOC_ID = "29088580780787855"

# Authentic Android Device Profiles
DEVICE_PROFILES = [
    {
        "deviceString": "26/8.0.0; 480dpi; 1080x1920; samsung; SM-G930F; herolte; samsungexynos8890",
        "language": "en_US",
    },
    {
        "deviceString": "33/13.0; 480dpi; 1080x2400; samsung; SM-G998B; galaxy-s21-ultra; exynos2100",
        "language": "en_US",
    },
    {
        "deviceString": "34/14; 500dpi; 1440x3088; samsung; SM-S918B; dm3q; qcom",
        "language": "en_US",
    },
    {
        "deviceString": "34/14; 560dpi; 1440x3120; Google; Pixel 7 Pro; cheetah; gs201",
        "language": "en_US",
    },
]


def log(level: str, message: str) -> None:
    """Internal engine log wrapper pushing into central telemetry."""
    add_log(level, message, "ENGINE", "system")


def get_cookie(session, name: str, default: str = "") -> str:
    """Safely retrieves cookie value without throwing CookieConflictError."""
    if not session or not hasattr(session, 'cookies'):
        return default
    try:
        for c in session.cookies:
            if c.name == name and c.value:
                return c.value
    except Exception:
        pass
    return default


def format_spam_message(text: str, prefix: str = "") -> str:
    """
    Exclusively for SPAM MESSAGES.
    - Uses prefix only.
    - Replaces (prefix), {prefix}, [prefix], <prefix>, %prefix% with prefix.
    - If prefix provided and no placeholder present, prepends prefix to the message.
    - Cleans out any stray (target) placeholders so 2 target names never get mixed into 1 message.
    - Target Name is NEVER mixed with Spam Messages!
    """
    if not text:
        return ""
    prefix_val = (prefix or "").strip()
    formatted = text

    # Strip out any legacy / stray (target) placeholders so they never duplicate
    target_pattern = re.compile(r"(\(target\)|{target}|\[target\]|<target>|%target%)", re.IGNORECASE)
    formatted = target_pattern.sub("", formatted)

    prefix_pattern = re.compile(r"(\(prefix\)|{prefix}|\[prefix\]|<prefix>|%prefix%)", re.IGNORECASE)
    if prefix_pattern.search(formatted):
        formatted = prefix_pattern.sub(prefix_val, formatted)
    elif prefix_val:
        if not formatted.lower().startswith(prefix_val.lower()):
            formatted = f"{prefix_val} {formatted}".strip()

    return re.sub(r" +", " ", formatted).strip()


def format_nc_title(title: str, target_name: str = "") -> str:
    """
    Exclusively for NC (Group Chat Renaming).
    - Uses target_name only.
    - Replaces (target), {target}, [target], <target>, %target% with target_name.
    - If target_name provided and no placeholder present, prepends target_name if title doesn't start with it.
    - Strips out any stray (prefix) placeholders.
    - Message Prefix is NEVER mixed with NC / Group Titles!
    """
    if not title:
        return ""
    target_val = (target_name or "").strip()
    formatted = title

    # Strip out any legacy (prefix) placeholders from group titles
    prefix_pattern = re.compile(r"(\(prefix\)|{prefix}|\[prefix\]|<prefix>|%prefix%)", re.IGNORECASE)
    formatted = prefix_pattern.sub("", formatted)

    target_pattern = re.compile(r"(\(target\)|{target}|\[target\]|<target>|%target%)", re.IGNORECASE)
    if target_pattern.search(formatted):
        formatted = target_pattern.sub(target_val, formatted)
    elif target_val:
        if not formatted.lower().startswith(target_val.lower()):
            formatted = f"{target_val} {formatted}".strip()

    return re.sub(r" +", " ", formatted).strip()


def format_target_template(text: str, target_name: str = "", prefix: str = "") -> str:
    """
    Backward-compatible wrapper:
    If prefix provided, treats text as spam message (no duplicate target).
    If target_name provided without prefix, treats as NC title.
    """
    if prefix and not target_name:
        return format_spam_message(text, prefix)
    elif target_name and not prefix:
        return format_nc_title(text, target_name)
    else:
        t = format_nc_title(text, target_name)
        return format_spam_message(t, prefix)


def load_lines_from_file(file_path: str, default_lines: list) -> list:
    """Loads non-empty lines from a text file, returning default_lines if file doesn't exist."""
    if not os.path.exists(file_path):
        return default_lines
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        return lines if lines else default_lines
    except Exception as e:
        log("WARN", f"Could not read {file_path}: {e}")
        return default_lines


def load_messages_from_file(file_path: str, default_messages: list) -> list:
    """
    Loads message templates from text file with full support for quotes, multi-line blocks, delimiters.
    """
    if not os.path.exists(file_path):
        return default_messages
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return default_messages

        blocks = []
        # Triple quotes
        triple_quotes = re.findall(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', content, re.DOTALL)
        if triple_quotes:
            for t1, t2 in triple_quotes:
                val = (t1 or t2).strip()
                if val:
                    blocks.append(val)

        # Standard or smart double quotes
        if not blocks:
            double_quotes = re.findall(r'["“](.*?)[”"]', content, re.DOTALL)
            if double_quotes:
                for q in double_quotes:
                    val = q.strip()
                    if val:
                        blocks.append(val)

        # Delimiters
        if not blocks:
            lines = content.splitlines()
            current = []
            has_delimiter = False
            for line in lines:
                if line.strip() in ("---", "===", "___", "###", "[SPLIT]"):
                    has_delimiter = True
                    if current:
                        blocks.append("\n".join(current).strip())
                        current = []
                else:
                    current.append(line)
            if current:
                blocks.append("\n".join(current).strip())

            blocks = [b for b in blocks if b]
            if not has_delimiter and not blocks:
                blocks = [content.strip()]

        return blocks if blocks else default_messages
    except Exception as e:
        log("WARN", f"Could not read {file_path}: {e}")
        return default_messages


def generate_device(username: str = "") -> tuple[str, str, str, str, str]:
    uname = (username or "instagram_user").strip().lower()
    h = hashlib.sha256(uname.encode('utf-8')).hexdigest()
    device_id = f"android-{h[:16]}"
    ns = uuid.UUID('2b4a1b0a-313d-4c3e-8c3b-1b0a313d4c3e')
    uuid_val = str(uuid.uuid5(ns, uname + "_uuid"))
    phone_id = str(uuid.uuid5(ns, uname + "_phone"))
    adid = str(uuid.uuid5(ns, uname + "_adid"))
    mid = f"Z{hashlib.md5((uname + '_machine_id').encode('utf-8')).hexdigest()[:20]}"
    return device_id, uuid_val, phone_id, adid, mid


def generate_device_profile(username: str = "") -> dict:
    uname = (username or "instagram_user").strip().lower()
    device_id, uuid_val, phone_id, adid, mid = generate_device(uname)
    prof_idx = int(hashlib.md5(uname.encode('utf-8')).hexdigest()[:8], 16) % len(DEVICE_PROFILES)
    dev = DEVICE_PROFILES[prof_idx]
    device_str = dev["deviceString"]
    language = dev["language"]
    user_agent = f"Instagram {IG_APP_VERSION} Android ({device_str}; {language}; {IG_VERSION_CODE})"

    return {
        "device_id": device_id,
        "android_id": device_id,
        "deviceId": device_id,
        "uuid": uuid_val,
        "phone_id": phone_id,
        "adid": adid,
        "mid": mid,
        "deviceString": device_str,
        "language": language,
        "user_agent": user_agent,
        "app_version": IG_APP_VERSION,
        "version_code": IG_VERSION_CODE,
        "app_id": IG_APP_ID,
        "capabilities": IG_CAPABILITIES,
        "bloks_version_id": BLOKS_VERSION_ID,
        "authorization": "",
        "ig_www_claim": "0",
    }


def build_bearer_token(ds_user_id: str, session_id: str) -> str:
    if not session_id:
        return ""
    bearer_data = {
        "ds_user_id": str(ds_user_id or ""),
        "sessionid": str(session_id or "")
    }
    json_data = json.dumps(bearer_data, separators=(',', ':'))
    encoded = base64.b64encode(json_data.encode('utf-8')).decode('utf-8')
    return f"Bearer IGT:2:{encoded}"


def build_mobile_headers(profile: dict, csrf_token: str = "", session=None) -> dict:
    device_id = profile.get("device_id") or profile.get("deviceId", f"android-{uuid.uuid4().hex[:16]}")
    uuid_val = profile.get("uuid", str(uuid.uuid4()))
    user_agent = profile.get("user_agent") or f"Instagram {IG_APP_VERSION} Android (34/14; 500dpi; 1440x3088; samsung; SM-S918B; dm3q; qcom; en_US; {IG_VERSION_CODE})"
    language = profile.get("language", "en_US")
    ig_www_claim = profile.get("ig_www_claim", "0")
    auth_header = profile.get("authorization", "")
    pigeon_session_id = str(uuid.uuid4())

    headers = {
        'User-Agent': user_agent,
        'X-Ads-Opt-Out': '0',
        'X-IG-App-Locale': language,
        'X-IG-Device-Locale': language,
        'X-Pigeon-Session-Id': pigeon_session_id,
        'X-Pigeon-Rawclienttime': f"{time.time():.3f}",
        'X-IG-Connection-Speed': f"{random.randint(1500, 4500)}kbps",
        'X-IG-Bandwidth-Speed-KBPS': '-1.000',
        'X-IG-Bandwidth-TotalBytes-B': '0',
        'X-IG-Bandwidth-TotalTime-MS': '0',
        'X-IG-Extended-CDN-Thumbnail-Cache-Busting-Value': '10000',
        'X-Bloks-Version-Id': BLOKS_VERSION_ID,
        'X-IG-WWW-Claim': str(ig_www_claim or '0'),
        'X-Bloks-Is-Layout-RTL': 'false',
        'X-IG-Connection-Type': 'WIFI',
        'X-IG-Capabilities': IG_CAPABILITIES,
        'X-IG-App-ID': IG_APP_ID,
        'X-IG-Device-ID': uuid_val,
        'X-IG-Android-ID': device_id,
        'Accept-Language': language.replace('_', '-'),
        'X-FB-HTTP-Engine': 'Liger',
        'Host': 'i.instagram.com',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    if csrf_token:
        headers["X-CSRFToken"] = csrf_token
    if auth_header:
        headers["Authorization"] = auth_header
    if session and hasattr(session, 'cookies'):
        cookie_parts = [f"{c.name}={c.value}" for c in session.cookies]
        if cookie_parts:
            headers['Cookie'] = "; ".join(cookie_parts)
    return headers


def build_headers(device_id: str, android_id: str, csrf_token: str = DEFAULT_CSRF_TOKEN) -> dict:
    profile = {"device_id": device_id, "android_id": android_id}
    return build_mobile_headers(profile, csrf_token=csrf_token)


def update_state_from_response(response, profile: dict):
    if not response or not hasattr(response, 'headers'):
        return
    headers = response.headers
    if 'x-ig-set-www-claim' in headers:
        profile['ig_www_claim'] = headers['x-ig-set-www-claim']
    if 'ig-set-authorization' in headers and not headers['ig-set-authorization'].endswith(':'):
        profile['authorization'] = headers['ig-set-authorization']


def verify_session(session: requests.Session, headers: dict) -> dict:
    """
    Dual verification: Direct Inbox Viewer info + Mobile current_user endpoint.
    """
    csrf_token = get_cookie(session, "csrftoken", headers.get("X-CSRFToken", DEFAULT_CSRF_TOKEN))

    # 1. Primary: Direct Inbox Viewer info
    try:
        inbox_url = "https://www.instagram.com/api/v1/direct_v2/inbox/?visual_message_return_type=unseen&thread_message_limit=10&persistentBadging=true&limit=20"
        web_headers = {
            "User-Agent": WEB_USER_AGENT,
            "X-IG-App-ID": IG_WEB_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/direct/inbox/",
            "X-CSRFToken": csrf_token,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = session.get(inbox_url, headers=web_headers, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if "viewer" in data and data["viewer"]:
                viewer = data["viewer"]
                if viewer.get("username") or viewer.get("pk"):
                    return {
                        "pk": str(viewer.get("pk") or viewer.get("id")),
                        "username": viewer.get("username", "Unknown"),
                        "full_name": viewer.get("full_name", ""),
                        "profile_pic_url": viewer.get("profile_pic_url", ""),
                        "is_verified": viewer.get("is_verified", False),
                    }
    except Exception as e:
        log("WARN", f"Direct inbox verification check: {e}")

    # 2. Secondary Fallback: Mobile current_user endpoint
    try:
        url = f"{API_BASE_URL}/accounts/current_user/?edit=true"
        r = session.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "ok" and "user" in data:
                return data["user"]
    except Exception as e:
        log("WARN", f"Mobile verification check: {e}")

    return {}


def fetch_group_chats(
    session: requests.Session,
    headers: dict,
    config: dict = None,
    client = None,
    existing_groups: list = None,
    thread_ids_hint: list = None
) -> list[dict]:
    """
    Exhaustively and accurately scans Instagram Direct to discover ALL group chats for the ID,
    ensuring all valid group chats with more than 1 user (users_count > 1) are fetched.
    Scans:
      1. Mobile API Primary Inbox (full cursor pagination with dict/string serialization, pinned_threads + threads)
      2. Mobile API General Folder (folder=1, professional/creator mode)
      3. Mobile API Pending Inbox (message requests & group invites)
      4. Mobile API Spam Inbox (hidden requests & auto-filtered group invites)
      5. Web Direct Primary Inbox (multi-page fallback)
      6. Web Direct Pending Inbox (message requests fallback)
      7. Web Direct Spam Inbox (hidden requests fallback)
      8. Instagrapi Client Engine (threads chunk, general chunk, pending chunk, spam chunk, direct channels)
      9. Single Thread / Hint direct fetch fallback
     10. Preserves previously discovered active groups meeting members > 1
    """
    groups_map = {}
    config = config or {}
    max_pages = max(100, config.get("advanced", {}).get("max_pagination_pages", 100))

    def process_raw_thread(t):
        if not isinstance(t, dict):
            return
        tid = str(t.get("thread_id") or t.get("thread_v2_id") or t.get("id") or t.get("pk") or "").strip()
        if not tid:
            return

        raw_users = t.get("users", []) or []
        users_list = [
            str(u.get("username")).strip()
            for u in raw_users
            if isinstance(u, dict) and u.get("username") and str(u.get("username")).strip()
        ]
        raw_recips = t.get("recipient_ids", []) or []

        # Accurate group chat detection (excludes 1-on-1 private DMs)
        is_group = bool(t.get("is_group"))
        thread_type = str(t.get("thread_type") or "").strip().lower()
        has_title = bool(t.get("thread_title") and str(t.get("thread_title")).strip())
        has_admins = bool(t.get("admin_user_ids"))

        if len(raw_users) >= 2 or len(raw_recips) >= 2:
            is_group = True
        if thread_type in ["group", "channel", "broadcast"]:
            is_group = True
        if has_title or has_admins:
            is_group = True

        # Pure 1-on-1 private DMs on Instagram must never be treated as group chats
        if not is_group:
            return

        other_users_count = max(len(raw_users), len(raw_recips), len(users_list))
        api_users_count = 0
        if t.get("users_count"):
            try:
                api_users_count = max(api_users_count, int(t.get("users_count")))
            except Exception:
                pass
        if t.get("user_count"):
            try:
                api_users_count = max(api_users_count, int(t.get("user_count")))
            except Exception:
                pass

        # Total members in this group chat:
        # Since the authenticated account is an active member in this group:
        # If other members exist, total members = other_users_count + 1.
        if other_users_count >= 1:
            total_members = max(other_users_count + 1, api_users_count, len(users_list) + 1 if users_list else 2)
        else:
            total_members = api_users_count if api_users_count > 0 else (1 if is_group else 0)

        # "id mai 1 se jada user wale jitne bhi groups hoga sabhi fetch hone chaiya"
        # Must have more than 1 user (> 1 user)
        if total_members <= 1 and other_users_count < 1 and api_users_count <= 1:
            return

        title = t.get("thread_title")
        if not title or not str(title).strip():
            if users_list:
                title = ", ".join(users_list[:3]) + ("..." if len(users_list) > 3 else " Group")
            else:
                title = f"Group {tid}"

        display_count = max(total_members, len(users_list))
        if display_count <= 1 and (other_users_count >= 1 or is_group):
            display_count = max(2, other_users_count + 1)

        groups_map[tid] = {
            "id": tid,
            "title": str(title).strip() or f"Group {tid}",
            "users_count": display_count,
            "users": users_list,
        }

    def process_instagrapi_thread(t):
        if not t:
            return
        tid = str(getattr(t, "id", "") or getattr(t, "pk", "") or "").strip()
        if not tid:
            return

        raw_users = getattr(t, "users", []) or []
        users_list = [
            str(getattr(u, "username", "")).strip()
            for u in raw_users
            if getattr(u, "username", "") and str(getattr(u, "username", "")).strip()
        ]

        is_group = bool(getattr(t, "is_group", False))
        thread_type = str(getattr(t, "thread_type", "") or "").strip().lower()
        has_title = bool(getattr(t, "thread_title", None) and str(getattr(t, "thread_title")).strip())
        has_admins = bool(getattr(t, "admin_user_ids", None))

        if len(raw_users) >= 2:
            is_group = True
        if thread_type in ["group", "channel", "broadcast"]:
            is_group = True
        if has_title or has_admins:
            is_group = True

        if not is_group:
            return

        other_users_count = max(len(raw_users), len(users_list))
        api_users_count = 0
        if hasattr(t, "users_count") and getattr(t, "users_count", None):
            try:
                api_users_count = int(getattr(t, "users_count"))
            except Exception:
                pass

        if other_users_count >= 1:
            total_members = max(other_users_count + 1, api_users_count, len(users_list) + 1 if users_list else 2)
        else:
            total_members = api_users_count if api_users_count > 0 else (1 if is_group else 0)

        if total_members <= 1 and other_users_count < 1 and api_users_count <= 1:
            return

        title = getattr(t, "thread_title", None)
        if not title or not str(title).strip():
            if users_list:
                title = ", ".join(users_list[:3]) + ("..." if len(users_list) > 3 else " Group")
            else:
                title = f"Group {tid}"

        display_count = max(total_members, len(users_list))
        if display_count <= 1 and (other_users_count >= 1 or is_group):
            display_count = max(2, other_users_count + 1)

        groups_map[tid] = {
            "id": tid,
            "title": str(title).strip() or f"Group {tid}",
            "users_count": display_count,
            "users": users_list,
        }

    def scan_endpoint(base_url, req_headers, label, max_p=max_pages):
        cursor = None
        page = 1
        consecutive_errors = 0
        seen_cursors = set()

        while page <= max_p:
            url = base_url
            if cursor:
                if isinstance(cursor, dict):
                    cursor_encoded = urllib.parse.quote(json.dumps(cursor, separators=(',', ':')))
                else:
                    cursor_encoded = urllib.parse.quote(str(cursor).strip())
                sep = "&" if "?" in url else "?"
                url += f"{sep}cursor={cursor_encoded}&direction=older"

            try:
                r = session.get(url, headers=req_headers, timeout=18)
                if r.status_code == 429:
                    time.sleep(2.0)
                    r = session.get(url, headers=req_headers, timeout=18)

                if r.status_code != 200:
                    consecutive_errors += 1
                    if consecutive_errors >= 2:
                        break
                    time.sleep(1.0)
                    continue

                consecutive_errors = 0
                data = r.json()
                inbox = data.get("inbox") if isinstance(data.get("inbox"), dict) else data

                # Process pinned threads and standard threads
                pinned = inbox.get("pinned_threads", []) or []
                threads = inbox.get("threads", []) or []
                all_raw = pinned + threads
                if not all_raw and isinstance(data.get("threads"), list):
                    all_raw = data.get("threads", [])

                for t in all_raw:
                    process_raw_thread(t)

                has_older = bool(inbox.get("has_older", False))
                next_c = inbox.get("oldest_cursor") or inbox.get("prev_cursor") or inbox.get("prev_cursor_v2") or inbox.get("cursor")

                if not has_older or not next_c:
                    break

                c_key = json.dumps(next_c, sort_keys=True) if isinstance(next_c, dict) else str(next_c)
                if c_key in seen_cursors:
                    break
                seen_cursors.add(c_key)

                cursor = next_c
                page += 1
                time.sleep(0.25)
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= 2:
                    break
                time.sleep(1.0)

    # 1. Primary Mobile API Inbox Scan
    scan_endpoint("https://i.instagram.com/api/v1/direct_v2/inbox/?persistentBadging=true&limit=50", headers, "Mobile Primary Inbox")

    # 2. General Folder Scan (folder=1, professional/creator accounts)
    scan_endpoint("https://i.instagram.com/api/v1/direct_v2/inbox/?persistentBadging=true&folder=1&limit=50", headers, "Mobile General Folder")

    # 3. Pending Requests / Group Invites Scan
    scan_endpoint("https://i.instagram.com/api/v1/direct_v2/pending_inbox/?persistentBadging=true&limit=50", headers, "Mobile Pending Inbox")

    # 4. Spam Requests / Auto-Filtered Invites Scan
    scan_endpoint("https://i.instagram.com/api/v1/direct_v2/spam_inbox/?persistentBadging=true&limit=50", headers, "Mobile Spam Inbox")

    # 5. Web Direct Inbox Scans
    try:
        csrf_token = get_cookie(session, "csrftoken", headers.get("X-CSRFToken", DEFAULT_CSRF_TOKEN))
        web_headers = {
            "User-Agent": WEB_USER_AGENT,
            "X-IG-App-ID": IG_WEB_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/direct/inbox/",
            "X-CSRFToken": csrf_token,
            "Accept": "*/*",
        }
        scan_endpoint("https://www.instagram.com/api/v1/direct_v2/inbox/?visual_message_return_type=unseen&thread_message_limit=10&persistentBadging=true&limit=50", web_headers, "Web Primary Inbox")
        scan_endpoint("https://www.instagram.com/api/v1/direct_v2/pending_inbox/?visual_message_return_type=unseen&thread_message_limit=10&persistentBadging=true&limit=50", web_headers, "Web Pending Inbox")
        scan_endpoint("https://www.instagram.com/api/v1/direct_v2/spam_inbox/?visual_message_return_type=unseen&thread_message_limit=10&persistentBadging=true&limit=50", web_headers, "Web Spam Inbox")
    except Exception:
        pass

    # 6. Instagrapi Client Scan Suite
    if client is not None:
        try:
            cl_cursor = None
            cl_pages = 0
            while cl_pages < max_pages:
                threads_chunk, cl_cursor = client.direct_threads_chunk(cursor=cl_cursor)
                if not threads_chunk:
                    break
                for t in threads_chunk:
                    process_instagrapi_thread(t)
                if not cl_cursor:
                    break
                cl_pages += 1
                time.sleep(0.2)
        except Exception:
            pass

        try:
            cl_cursor = None
            cl_pages = 0
            while cl_pages < max_pages:
                threads_chunk, cl_cursor = client.direct_threads_chunk(box="general", cursor=cl_cursor)
                if not threads_chunk:
                    break
                for t in threads_chunk:
                    process_instagrapi_thread(t)
                if not cl_cursor:
                    break
                cl_pages += 1
                time.sleep(0.2)
        except Exception:
            pass

        try:
            cl_cursor = None
            cl_pages = 0
            while cl_pages < max_pages:
                threads_chunk, cl_cursor = client.direct_pending_chunk(cursor=cl_cursor)
                if not threads_chunk:
                    break
                for t in threads_chunk:
                    process_instagrapi_thread(t)
                if not cl_cursor:
                    break
                cl_pages += 1
                time.sleep(0.2)
        except Exception:
            pass

        try:
            cl_cursor = None
            cl_pages = 0
            while cl_pages < max_pages:
                threads_chunk, cl_cursor = client.direct_spam_chunk(cursor=cl_cursor)
                if not threads_chunk:
                    break
                for t in threads_chunk:
                    process_instagrapi_thread(t)
                if not cl_cursor:
                    break
                cl_pages += 1
                time.sleep(0.2)
        except Exception:
            pass

        try:
            channels = client.direct_channels()
            if channels and isinstance(channels, list):
                for ch in channels:
                    if isinstance(ch, dict):
                        process_raw_thread(ch)
        except Exception:
            pass

    # 7. Single Thread / Hint Direct Query Fallback
    if thread_ids_hint:
        for hid in thread_ids_hint:
            hid_str = str(hid).strip()
            if hid_str and hid_str not in groups_map:
                try:
                    r = session.get(f"https://i.instagram.com/api/v1/direct_v2/threads/{hid_str}/", headers=headers, timeout=12)
                    if r.status_code == 200:
                        t_data = r.json().get("thread", {})
                        if t_data:
                            process_raw_thread(t_data)
                except Exception:
                    pass

    # 8. Merge with previously saved groups (preserving valid GCs with members > 1)
    if existing_groups and isinstance(existing_groups, list):
        for eg in existing_groups:
            if not isinstance(eg, dict):
                continue
            eg_id = str(eg.get("id", "")).strip()
            if not eg_id:
                continue
            eg_count = int(eg.get("users_count") or len(eg.get("users", [])) or 0)
            if eg_count > 1 or len(eg.get("users", [])) >= 1:
                if eg_id not in groups_map:
                    groups_map[eg_id] = {
                        "id": eg_id,
                        "title": str(eg.get("title") or f"Group {eg_id}").strip(),
                        "users_count": max(eg_count, len(eg.get("users", [])), 2 if len(eg.get("users", [])) >= 1 else 2),
                        "users": eg.get("users", [])
                    }

    groups_list = list(groups_map.values())
    whitelist = set(config.get("target_thread_ids_whitelist", [])) if config else set()
    if whitelist:
        groups_list = [g for g in groups_list if g["id"] in whitelist]

    # Final guarantee: Only keep group chats where members > 1 (all groups with more than 1 user)
    groups_list = [
        g for g in groups_list 
        if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1
    ]

    add_log("SUCCESS", f"Total Valid Group Chats Discovered (>1 members): {len(groups_list)}", "", "multi")
    return groups_list


def clean_api_error(status_code: int, raw_text: str = "") -> str:
    """Sanitizes raw API response into a clean, human-readable error string, stripping raw HTML."""
    if not raw_text:
        return f"HTTP {status_code}"
    text = str(raw_text).strip()
    if "<!doctype" in text.lower() or "<html" in text.lower() or "not-logged-in" in text.lower():
        if status_code == 404:
            return "HTTP 404 (Group DP change endpoint is restricted/closed by Meta on REST API)"
        elif status_code == 403:
            return "HTTP 403 (Session expired or mobile authorization required)"
        elif status_code == 429:
            return "HTTP 429 (Rate limit reached - cooling down session)"
        elif status_code == 500:
            return "HTTP 500 (Instagram server issue - auto-retrying)"
        return f"HTTP {status_code} (Instagram session/permission rejected)"
    try:
        rj = json.loads(text)
        msg = rj.get("message") or rj.get("feedback_message") or rj.get("error_title")
        if msg:
            return f"Instagram: {msg}"
    except Exception:
        pass
    text_clean = text.replace("<", "").replace(">", "").strip()
    return f"HTTP {status_code} - {text_clean[:65]}"


def send_message_to_group(session: requests.Session, headers: dict, thread_id: str, text: str, config: dict, client = None) -> tuple[bool, str]:
    """Sends text message to group using instagrapi (mobile), Mobile Direct API, and Web Direct fallbacks."""
    # 1. Primary: Instagrapi mobile client (Authentic Android App signatures - Prevents account logouts)
    if client is not None:
        try:
            client.request_timeout = 10.0
            try:
                res = client.direct_send(text, thread_ids=[str(thread_id)])
            except Exception:
                res = client.direct_send(text, thread_ids=[int(thread_id)])
            if res:
                return True, "Delivered"
        except Exception as e:
            err_str = str(e).lower()
            if "login_required" in err_str or "checkpoint" in err_str:
                return False, "Login Required"
            pass

    csrf_token = get_cookie(session, "csrftoken", headers.get("X-CSRFToken", DEFAULT_CSRF_TOKEN))
    client_context = str(uuid.uuid4())

    # 2. Secondary: Mobile API Direct Endpoint
    csrf_token = get_cookie(session, "csrftoken", csrf_token)
    mobile_url = "https://i.instagram.com/api/v1/direct_v2/threads/broadcast/text/"
    post_headers = headers.copy()
    post_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    post_headers["X-CSRFToken"] = csrf_token
    device_id = headers.get("X-IG-Device-ID", str(uuid.uuid4()))
    android_id = headers.get("X-IG-Android-ID", f"android-{uuid.uuid4().hex[:16]}")

    mobile_payload = {
        "action": "send_item",
        "thread_ids": json.dumps([str(thread_id)]),
        "text": text,
        "client_context": client_context,
        "_uuid": device_id,
        "_csrftoken": csrf_token,
        "device_id": android_id,
        "mutation_token": client_context,
        "offline_threading_id": client_context,
    }

    try:
        r = session.post(mobile_url, headers=post_headers, data=mobile_payload, timeout=(4.0, 10.0))
        if hasattr(r, 'cookies') and r.cookies:
            session.cookies.update(r.cookies)
        if r.status_code == 200:
            return True, "Delivered"
        elif r.status_code == 429:
            return False, "Rate Limited (HTTP 429)"
        elif r.status_code in (401, 403) and "login_required" in r.text.lower():
            return False, "Login Required"
        else:
            err_txt = clean_api_error(r.status_code, r.text)
            if "login_required" in err_txt.lower():
                return False, "Login Required"
    except Exception:
        pass

    # 3. Tertiary: Web Direct API Fallback
    web_headers = {
        "User-Agent": WEB_USER_AGENT,
        "X-IG-App-ID": IG_WEB_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/direct/t/{thread_id}/",
        "X-CSRFToken": csrf_token,
        "Origin": "https://www.instagram.com",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    web_payload = {
        "action": "send_item",
        "thread_ids": f'["{thread_id}"]',
        "text": text,
        "client_context": client_context,
        "mutation_token": client_context,
        "offline_threading_id": client_context,
    }

    try:
        r_web = session.post(
            "https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/",
            headers=web_headers,
            data=web_payload,
            timeout=(4.0, 10.0)
        )
        if hasattr(r_web, 'cookies') and r_web.cookies:
            session.cookies.update(r_web.cookies)
        if r_web.status_code == 200:
            try:
                resp_json = r_web.json()
                if resp_json.get("status") == "ok":
                    return True, "Delivered"
                elif resp_json.get("message") == "checkpoint_required":
                    return False, "Checkpoint Required"
                elif resp_json.get("message") == "login_required":
                    return False, "Login Required"
            except Exception:
                return True, "Delivered"
            return True, "Delivered"
        elif r_web.status_code == 429:
            return False, "Rate Limited (HTTP 429)"
        elif r_web.status_code in (400, 401, 403):
            try:
                resp_json = r_web.json()
                if "checkpoint_required" in str(resp_json) or "feedback_required" in str(resp_json):
                    return False, "Action Block / Checkpoint Triggered"
                elif "login_required" in str(resp_json):
                    return False, "Login Required"
            except Exception:
                pass
    except Exception:
        pass

    return False, "Dispatch timeout / temporary network stall"


def rename_group_chat(session: requests.Session, headers: dict, thread_id: str, new_title: str, config: dict, client = None) -> tuple[bool, str]:
    """Renames group chat using authentic instagrapi, GraphQL, and Web/Mobile Direct API fallbacks."""
    # 1. Primary: Instagrapi client (Authentic mobile update_title)
    if client is not None:
        try:
            client.request_timeout = 10.0
            try:
                res = client.direct_thread_update_title(str(thread_id), new_title)
            except Exception:
                res = client.direct_thread_update_title(int(thread_id), new_title)
            if res:
                return True, new_title
        except Exception as e:
            err_str = str(e).lower()
            if "login_required" in err_str or "checkpoint" in err_str:
                return False, "Login Required"
            pass

    csrf_token = get_cookie(session, "csrftoken", headers.get("X-CSRFToken", DEFAULT_CSRF_TOKEN))

    # 2. Secondary: Instagram GraphQL API Rename
    graphql_headers = {
        "User-Agent": WEB_USER_AGENT,
        "X-CSRFToken": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/direct/t/{thread_id}/",
        "Origin": "https://www.instagram.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
    }
    graphql_payload = {
        "doc_id": GRAPHQL_DOC_ID,
        "variables": json.dumps({
            "thread_fbid": str(thread_id),
            "new_title": new_title,
        }),
    }

    try:
        r_gql = session.post("https://www.instagram.com/api/graphql/", headers=graphql_headers, data=graphql_payload, timeout=(3.0, 8.0))
        if hasattr(r_gql, 'cookies') and r_gql.cookies:
            session.cookies.update(r_gql.cookies)
        if r_gql.status_code == 200:
            try:
                data = r_gql.json()
                if not data.get("errors") and not data.get("error"):
                    return True, new_title
            except Exception:
                return True, new_title
        elif r_gql.status_code == 429:
            return False, "Rate Limited (HTTP 429)"
        elif r_gql.status_code in (401, 403) and "login_required" in r_gql.text.lower():
            return False, "Login Required"
    except Exception:
        pass

    # 3. Tertiary: Web Direct update_title
    web_headers = {
        "User-Agent": WEB_USER_AGENT,
        "X-IG-App-ID": IG_WEB_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/direct/t/{thread_id}/",
        "X-CSRFToken": csrf_token,
        "Origin": "https://www.instagram.com",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    web_url = f"https://www.instagram.com/api/v1/direct_v2/threads/{thread_id}/update_title/"

    try:
        r_web = session.post(web_url, headers=web_headers, data={"title": new_title}, timeout=(3.0, 8.0))
        if hasattr(r_web, 'cookies') and r_web.cookies:
            session.cookies.update(r_web.cookies)
        if r_web.status_code == 200:
            return True, new_title
        elif r_web.status_code == 429:
            return False, "Rate Limited (HTTP 429)"
        elif r_web.status_code in (400, 401, 403):
            try:
                resp_json = r_web.json()
                if "checkpoint_required" in str(resp_json) or "feedback_required" in str(resp_json):
                    return False, "Action Block / Checkpoint Triggered"
                elif "login_required" in str(resp_json):
                    return False, "Login Required"
            except Exception:
                pass
    except Exception:
        pass

    # 4. Quaternary: Mobile Direct update_title Fallback
    url = f"https://i.instagram.com/api/v1/direct_v2/threads/{thread_id}/update_title/"
    post_headers = headers.copy()
    post_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    post_headers["X-CSRFToken"] = csrf_token
    device_id = headers.get("X-IG-Device-ID", "")
    payload = {"title": new_title, "_uuid": device_id, "_csrftoken": csrf_token}

    try:
        r = session.post(url, headers=post_headers, data=payload, timeout=(3.0, 8.0))
        if hasattr(r, 'cookies') and r.cookies:
            session.cookies.update(r.cookies)
        if r.status_code == 200:
            return True, new_title
        elif r.status_code == 429:
            return False, "Rate Limited (HTTP 429)"
        elif r.status_code in (401, 403) and "login_required" in r.text.lower():
            return False, "Login Required"
    except Exception:
        pass

    return False, "NC update limit reached or temporary network error"


def create_instagram_session(username: str, password: str) -> tuple[bool, dict, str]:
    """Authenticates with Instagram using Username and Password. Returns (success, session_dict, error_msg)."""
    session = requests.Session()
    device_id = str(uuid.uuid4())
    android_id = f"android-{uuid.uuid4().hex[:16]}"
    init_url = "https://www.instagram.com/accounts/login/"
    headers_init = {
        "User-Agent": WEB_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    csrf_token = DEFAULT_CSRF_TOKEN
    try:
        session.get(init_url, headers=headers_init, timeout=10)
        csrf_token = session.cookies.get("csrftoken", DEFAULT_CSRF_TOKEN)
    except Exception as e:
        log("WARN", f"Initial CSRF fetch warning: {e}")

    login_url = "https://www.instagram.com/api/v1/web/accounts/login/ajax/"
    login_headers = {
        "User-Agent": WEB_USER_AGENT,
        "X-IG-App-ID": IG_WEB_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/accounts/login/",
        "X-CSRFToken": csrf_token,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.instagram.com",
        "Accept": "*/*",
    }
    timestamp = int(time.time())
    payload = {
        "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{timestamp}:{password}",
        "username": username.strip(),
        "queryParams": "{}",
        "optIntoOneTap": "false",
        "trustedDeviceRecords": "{}",
    }

    try:
        r = session.post(login_url, headers=login_headers, data=payload, timeout=15)
        data = r.json()
        if data.get("authenticated") is True:
            session_id = session.cookies.get("sessionid", "")
            csrf_token = session.cookies.get("csrftoken", csrf_token)
            ds_user_id = str(data.get("userId") or session.cookies.get("ds_user_id", ""))
            return True, {
                "session_id": session_id,
                "csrf_token": csrf_token,
                "ds_user_id": ds_user_id,
                "device_id": device_id,
                "android_id": android_id,
                "user_info": {"pk": ds_user_id, "username": username, "full_name": ""},
            }, ""
        elif data.get("two_factor_required"):
            return False, {}, "Two-Factor Authentication (2FA) required. Please use Session ID."
        elif data.get("checkpoint_url"):
            return False, {}, "Instagram Checkpoint / Challenge triggered."
        elif data.get("user") is False:
            return False, {}, "Username not found. Please check spelling."
        elif data.get("message") == "checkpoint_required":
            return False, {}, "Checkpoint required. Please log in via browser first."

        # Fallback: instagrapi Mobile API Login
        try:
            cl = Client()
            cl.delay_range = [0, 1]
            cl.login(username.strip(), password)
            settings = cl.get_settings()
            auth_data = settings.get("authorization_data", {})
            session_id = auth_data.get("sessionid", "") or cl.session.cookies.get("sessionid", "")
            ds_user_id = str(auth_data.get("ds_user_id", "") or cl.user_id or "")
            if session_id:
                return True, {
                    "session_id": session_id,
                    "csrf_token": cl.session.cookies.get("csrftoken", DEFAULT_CSRF_TOKEN),
                    "ds_user_id": ds_user_id,
                    "device_id": device_id,
                    "android_id": android_id,
                    "user_info": {"pk": ds_user_id, "username": username, "full_name": ""},
                }, ""
        except Exception as cl_err:
            log("WARN", f"Instagrapi login attempt: {cl_err}")

        return False, {}, data.get("message", "Invalid username or password.")
    except Exception as e:
        try:
            cl = Client()
            cl.delay_range = [0, 1]
            cl.login(username.strip(), password)
            settings = cl.get_settings()
            auth_data = settings.get("authorization_data", {})
            session_id = auth_data.get("sessionid", "") or cl.session.cookies.get("sessionid", "")
            ds_user_id = str(auth_data.get("ds_user_id", "") or cl.user_id or "")
            if session_id:
                return True, {
                    "session_id": session_id,
                    "csrf_token": cl.session.cookies.get("csrftoken", DEFAULT_CSRF_TOKEN),
                    "ds_user_id": ds_user_id,
                    "device_id": device_id,
                    "android_id": android_id,
                    "user_info": {"pk": ds_user_id, "username": username, "full_name": ""},
                }, ""
        except Exception:
            pass
        return False, {}, f"Login request exception: {e}"


app = Flask(__name__)

# ================= SELF-PING / HEALTH WATCHDOG =================
# Keeps the web service reachable when an external uptime monitor is used.
# It does NOT refresh or extend Instagram sessions.
SELF_URL = os.getenv("SELF_URL", "").strip().rstrip("/")
SELF_PING_INTERVAL = max(30, int(os.getenv("SELF_PING_INTERVAL", "300")))

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "scar-dec",
        "timestamp": int(time.time())
    })

def self_ping_worker():
    if not SELF_URL:
        print("[SELF-PING] SELF_URL is not set; self-ping disabled.", flush=True)
        return

    url = f"{SELF_URL}/health"
    while True:
        try:
            response = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "ScarDec-SelfPing/1.0"}
            )
            if response.ok:
                print(
                    f"[SELF-PING] SUCCESS {response.status_code} -> {url}",
                    flush=True
                )
            else:
                print(
                    f"[SELF-PING] HTTP {response.status_code} -> {url}",
                    flush=True
                )
        except Exception as exc:
            print(f"[SELF-PING] ERROR -> {exc}", flush=True)

        time.sleep(SELF_PING_INTERVAL)

threading.Thread(
    target=self_ping_worker,
    name="self-ping",
    daemon=True
).start()

app.secret_key = "ULTRA_PANEL_KEY_KINGX_SCAR_CLAN"
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

DB_FILE = "db.json"
DB_BAK = "db.json.bak"
db_lock = threading.RLock()

# Central Telemetry Logs Buffer with Multi/Single/GC-Creator categorization
logs_lock = threading.Lock()
telemetry_logs = []

def add_log(level, msg, account="", category="system"):
    now_str = time.strftime("%H:%M:%S")
    entry = {
        "time": now_str,
        "timestamp": f"{time.strftime('%Y-%m-%d')} {now_str}",
        "level": str(level).upper(),
        "msg": str(msg),
        "message": str(msg),
        "account": str(account),
        "category": str(category).lower()
    }
    with logs_lock:
        telemetry_logs.append(entry)
        if len(telemetry_logs) > 1500:
            telemetry_logs.pop(0)

    # Realtime terminal output for powershell/cmd
    try:
        acc_tag = f"[@{account}]" if account else "[SYSTEM]"
        term_line = f"[{now_str}] [{str(level).upper()}] {acc_tag} {msg}"
        try:
            print(term_line, flush=True)
        except UnicodeEncodeError:
            print(term_line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    except Exception:
        pass

# Runtime state in memory for Premium / Multi / Single Bot
account_sessions = {}  # uid -> dict of {session, headers, profile, client, config}
bot_running = {}       # uid -> bool
stats = {}             # uid -> dict
live_stats = {}        # uid -> dict

# Non-Stop Engine: Active thread registry, Circuit Breakers & In-Memory Fallbacks
active_worker_threads = {}   # uid -> threading.Thread
rename_cooldowns = {}        # uid -> float timestamp when rename is allowed again
cached_account_configs = {}  # uid -> dict of latest known account settings

def safe_sleep(duration: float, uid: str = None) -> bool:
    """
    High-precision interruptible sleep.
    Returns True if full duration completed, False if cancelled by bot_running=False.
    Never blocks panel stop commands or gets stuck in infinite loops.
    """
    if duration <= 0:
        return True
    end = time.time() + duration
    while time.time() < end:
        if uid and not bot_running.get(str(uid), False):
            return False
        remaining = end - time.time()
        time.sleep(min(0.25, max(0.01, remaining)))
    return True

def report_login_required(uid, username, reason="Session expired or invalidated"):
    """
    Prominently outputs [!] LOGIN REQUIRED in the terminal and logs it to panel telemetry.
    """
    banner = f"\n{'='*70}\n[!] LOGIN REQUIRED: Account @{username} (UID #{uid})\n    Status: LOGGED OUT / SESSION EXPIRED\n    Reason: {reason}\n    Action: Please update Session ID in Panel!\n{'='*70}\n"
    try:
        sys.stdout.write(banner)
        sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.write(banner)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        add_log("ERROR", f"LOGIN REQUIRED: Session expired/logged out for @{username} (UID #{uid}) - {reason}", username, "system")
    except Exception:
        pass

# Runtime state in memory for GC Creator
gc_clients = {}
gc_running = {}
gc_stats = {}
gc_live = {}

# Runtime state in memory for Group Photo Changer
photo_running = {}       # uid -> bool
photo_stats = {}         # uid -> dict
photo_changer_files = {} # uid -> file_path
TEMP_PHOTOS_DIR = "temp_photos"
if not os.path.exists(TEMP_PHOTOS_DIR):
    try:
        os.makedirs(TEMP_PHOTOS_DIR, exist_ok=True)
    except Exception:
        pass

def cleanup_temp_photos():
    if os.path.exists(TEMP_PHOTOS_DIR):
        for f in os.listdir(TEMP_PHOTOS_DIR):
            try:
                os.remove(os.path.join(TEMP_PHOTOS_DIR, f))
            except Exception:
                pass

cleanup_temp_photos()

# Default DB template
DEFAULT_DB = {
    "users": {},
    "members": [],
    "accounts": {},
    "gc_accounts": {}
}


# ================= HIGH-PERFORMANCE THREAD-SAFE DB HELPERS =================
_db_cache = None
_db_cache_time = 0.0
_db_cache_lock = threading.Lock()

def load_db(force_reload=False):
    """
    High-performance DB loader with 1.5s in-memory caching.
    Prevents file lock contention and disk thrashing when hundreds/thousands of accounts run concurrently.
    """
    global _db_cache, _db_cache_time
    now = time.time()

    with _db_cache_lock:
        if not force_reload and _db_cache is not None and (now - _db_cache_time) < 1.5:
            return json.loads(json.dumps(_db_cache))

    with db_lock:
        if not os.path.exists(DB_FILE):
            save_db(DEFAULT_DB)
            return json.loads(json.dumps(DEFAULT_DB))

        for attempt in range(5):
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if not content:
                        raise ValueError("Empty DB file")
                    db = json.loads(content)
                    db.setdefault("users", DEFAULT_DB["users"].copy())
                    db.setdefault("members", DEFAULT_DB["members"].copy())
                    db.setdefault("accounts", {})
                    db.setdefault("gc_accounts", {})
                    with _db_cache_lock:
                        _db_cache = db
                        _db_cache_time = time.time()
                    return db
            except Exception:
                time.sleep(0.04 * (attempt + 1))

        if os.path.exists(DB_BAK):
            try:
                with open(DB_BAK, "r", encoding="utf-8") as f:
                    db = json.load(f)
                    db.setdefault("users", DEFAULT_DB["users"].copy())
                    db.setdefault("members", DEFAULT_DB["members"].copy())
                    db.setdefault("accounts", {})
                    db.setdefault("gc_accounts", {})
                    with _db_cache_lock:
                        _db_cache = db
                        _db_cache_time = time.time()
                    return db
            except Exception:
                pass

        with _db_cache_lock:
            if _db_cache is not None:
                return json.loads(json.dumps(_db_cache))
        return json.loads(json.dumps(DEFAULT_DB))


def save_db(data):
    """
    Thread-safe and atomic database writer.
    Immediately updates in-memory cache so all active workers see fresh state instantly.
    """
    global _db_cache, _db_cache_time
    with db_lock:
        with _db_cache_lock:
            _db_cache = json.loads(json.dumps(data))
            _db_cache_time = time.time()

        tmp_file = DB_FILE + f".tmp_{uuid.uuid4().hex[:6]}"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            if os.path.exists(DB_FILE):
                try:
                    shutil.copyfile(DB_FILE, DB_BAK)
                except Exception:
                    pass

            os.replace(tmp_file, DB_FILE)
        except Exception:
            try:
                with open(DB_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as err:
                print(f"Critical Error in save_db: {err}")
        finally:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass


def next_uid(db):
    accounts = db.get("accounts", {})
    if not accounts:
        return "1"
    nums = []
    for k in accounts.keys():
        try:
            nums.append(int(k))
        except:
            pass
    return str(max(nums) + 1) if nums else "1"


def next_gc_uid(db):
    gc_accounts = db.get("gc_accounts", {})
    if not gc_accounts:
        return "1"
    nums = []
    for k in gc_accounts.keys():
        try:
            nums.append(int(k.replace("gc_", "")))
        except:
            pass
    return str(max(nums) + 1) if nums else "1"


# ================= LINK & THREAD ID PARSER =================
def extract_thread_id(raw_input: str) -> str:
    """
    Extracts the numeric/alphanumeric Instagram Direct Thread ID from:
    1. Full Instagram web link: https://www.instagram.com/direct/t/340282366841710301281153328327707347074/
    2. Mobile/Short link: https://instagram.com/direct/t/17841412345678901
    3. Direct Inbox link: https://www.instagram.com/direct/inbox/17841412345678901
    4. Share link: https://ig.me/m/17841412345678901
    5. URL with query params: ...?id=123 or thread_id=123
    6. Raw Thread ID digits: 340282366841710301281153328327707347074
    """
    if not raw_input:
        return ""
    text = str(raw_input).strip().strip("\"' ")

    # Direct match for /direct/t/<id> or /direct/inbox/<id>
    m1 = re.search(r'/direct/(?:t|inbox)/([0-9a-zA-Z_\-]+)', text)
    if m1:
        return m1.group(1).strip()

    # Query param: thread_id=<id> or id=<id>
    m2 = re.search(r'[?&](?:thread_id|id)=([0-9a-zA-Z_\-]+)', text)
    if m2:
        return m2.group(1).strip()

    # Short /m/<id>
    m3 = re.search(r'/m/([0-9a-zA-Z_\-]+)', text)
    if m3:
        return m3.group(1).strip()

    # If full URL without matching above patterns, strip parameters and get last segment
    if "/" in text:
        clean = text.split("?")[0].rstrip("/")
        last_seg = clean.split("/")[-1]
        if last_seg and len(last_seg) >= 4 and last_seg.replace("-", "").isalnum():
            return last_seg

    # Fallback to pure digits or raw string
    digits_match = re.search(r'(\d{6,})', text)
    if digits_match:
        return digits_match.group(1).strip()

    return text


# ================= SESSION & AUTH MANAGEMENT (HYBRID REQUESTS + INSTAGRAPI) =================
def init_account_session(uid, sessionid, csrf_token="", username=""):
    """
    Initializes authentic Android device fingerprint, Requests cookie jar, and Instagrapi client.
    Performs dual-verification with Web Direct Inbox & Mobile Current User endpoints.
    """
    session_id = urllib.parse.unquote(str(sessionid or "").strip())
    ds_user_id = session_id.split(":")[0] if ":" in session_id else ""
    csrf = csrf_token or DEFAULT_CSRF_TOKEN
    uname = (username or f"user_{uid}").strip()

    profile = generate_device_profile(uname)
    if ds_user_id:
        profile["authorization"] = build_bearer_token(ds_user_id, session_id)

    # 1. Setup Requests Session with High-Concurrency HTTP Adapter
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=1)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    mid = profile.get("mid") or f"Z{uuid.uuid4().hex[:20]}"
    uuid_val = profile.get("uuid") or str(uuid.uuid4())

    s.cookies.set("sessionid", session_id, domain=".instagram.com", path="/")
    s.cookies.set("csrftoken", csrf, domain=".instagram.com", path="/")
    s.cookies.set("rur", "NAO", domain=".instagram.com", path="/")
    s.cookies.set("ig_did", uuid_val.upper(), domain=".instagram.com", path="/")
    s.cookies.set("mid", mid, domain=".instagram.com", path="/")
    s.cookies.set("ig_nrcb", "1", domain=".instagram.com", path="/")
    s.cookies.set("dpr", "2", domain=".instagram.com", path="/")
    if ds_user_id:
        s.cookies.set("ds_user_id", str(ds_user_id), domain=".instagram.com", path="/")

    headers = build_mobile_headers(profile, csrf_token=csrf, session=s)

    # 2. Setup Instagrapi Client (with strict 10s timeout to prevent socket hangs)
    cl = Client()
    cl.delay_range = [0, 1]
    cl.request_timeout = 10.0
    settings_dict = {
        "authorization_data": {
            "ds_user_id": str(ds_user_id),
            "sessionid": session_id,
            "should_use_header_over_cookies": True,
        },
        "cookies": {
            "sessionid": session_id,
            "csrftoken": csrf,
            "ds_user_id": str(ds_user_id),
            "mid": mid,
            "ig_did": uuid_val.upper(),
            "rur": "NAO",
        },
        "user_agent": profile.get("user_agent", ""),
        "uuids": {
            "phone_id": profile.get("phone_id", ""),
            "uuid": uuid_val,
            "client_session_id": uuid_val,
            "advertising_id": profile.get("adid", ""),
            "android_device_id": profile.get("device_id", ""),
        }
    }
    try:
        # Full login with session ID configures headers, authorization & cookies authentically
        cl.login_by_sessionid(session_id)
    except Exception as e:
        err_s = str(e).lower()
        if "login_required" in err_s or "checkpoint" in err_s:
            report_login_required(uid, uname, f"Instagrapi session login failed: {e}")
        try:
            cl.set_settings(settings_dict)
            cl.init()
            if ds_user_id:
                cl.authorization_data = {
                    "ds_user_id": str(ds_user_id),
                    "sessionid": session_id,
                    "should_use_header_over_cookies": True
                }
            cl.private.cookies.set("sessionid", session_id, domain=".instagram.com")
            cl.private.cookies.set("ds_user_id", str(ds_user_id), domain=".instagram.com")
            cl.private.cookies.set("csrftoken", csrf, domain=".instagram.com")
            if profile.get("authorization"):
                cl.private.headers["Authorization"] = profile["authorization"]
        except Exception:
            pass

    # Verify session using authentic dual-verification
    user_info = verify_session(s, headers)
    if not user_info:
        # Fallback to instagrapi check
        try:
            acc_info = cl.account_info()
            user_info = {
                "pk": str(acc_info.pk),
                "username": str(acc_info.username),
                "full_name": str(acc_info.full_name or ""),
                "profile_pic_url": str(acc_info.profile_pic_url or "")
            }
        except Exception as e:
            err_s = str(e).lower()
            if "login_required" in err_s or "checkpoint" in err_s:
                report_login_required(uid, uname, f"Session verification failed: {e}")

    resolved_username = (user_info.get("username") or uname).replace("@", "")
    resolved_pk = str(user_info.get("pk") or ds_user_id or "")

    account_sessions[str(uid)] = {
        "session": s,
        "headers": headers,
        "profile": profile,
        "client": cl,
        "config": {"advanced": {"max_pagination_pages": 50}},
        "username": resolved_username,
        "pk": resolved_pk,
        "user_info": user_info
    }

    return account_sessions[str(uid)]


def get_account_session(uid):
    uid_str = str(uid)
    if uid_str in account_sessions:
        return account_sessions[uid_str]

    db = load_db()
    acc = db.get("accounts", {}).get(uid_str)
    if acc and acc.get("sessionid"):
        try:
            return init_account_session(
                uid=uid_str,
                sessionid=acc.get("sessionid"),
                csrf_token=acc.get("csrf_token", ""),
                username=acc.get("username", "")
            )
        except Exception as e:
            err_str = str(e).lower()
            if "login_required" in err_str or "checkpoint" in err_str:
                report_login_required(uid_str, acc.get("username", uid_str), f"Session restore failed: {e}")
            add_log("ERROR", f"Failed to restore session for UID #{uid}: {e}", acc.get("username", uid), "system")
    return None


def load_messages():
    return load_messages_from_file("message.txt", ["Hey everyone! Welcome! 🚀", "Hope you are having a wonderful day!"])


def load_renames():
    return load_lines_from_file("nc.txt", ["⚡ (target) SUPREME GC ⚡", "🔥 (target) ELITE CLAN 🔥"])


# ================= LOGIN & DASHBOARD ROUTES =================
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if "role" in session:
            return redirect("/dashboard")
        return render_template("login.html")

    db = load_db()
    owner_pass = request.form.get("ownerpass", "").strip()
    master_passes = ["SCAR@12345"]
    if os.getenv("OWNER_PASSWORD"):
        master_passes.append(os.getenv("OWNER_PASSWORD").strip())

    if owner_pass:
        if owner_pass in master_passes:
            session.clear()
            session["role"] = "owner"
            session["user"] = "OWNER"
            session["name"] = "MASTER OWNER"
            add_log("SUCCESS", "👑 Master Owner logged into system with FULL ACCESS", "OWNER", "system")
            return redirect("/dashboard")
        else:
            return render_template("login.html", error="Invalid Master Owner Key! Access denied.")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    # Standard users must be created by Owner in db["users"]
    for u, udata in db.get("users", {}).items():
        if u.upper() == username.upper() and password == udata.get("password"):
            session.clear()
            session["role"] = "user"
            session["user"] = u
            session["name"] = udata.get("name", u)
            add_log("INFO", f"User @{u} authenticated", u, "system")
            return redirect("/dashboard")

    return render_template("login.html", error="Invalid Username or Password! Please verify your login credentials.")


@app.route("/logout")
def logout():
    u = session.get("user", "Someone")
    add_log("INFO", f"{u} logged out", u, "system")
    session.clear()
    return redirect("/")


@app.route("/dashboard")
@app.route("/premium")
@app.route("/mode")
def dashboard():
    if "role" not in session:
        return redirect("/")
    return render_template(
        "dashboard.html",
        role=session.get("role"),
        name=session.get("name"),
        user=session.get("user")
    )


# ================= CLIENT DOWNLOADS (MOD APK & ISO SUITE) =================
@app.route("/download/apk")
def download_apk():
    cur_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    apk_path = os.path.join(cur_dir, "static", "downloads", "server_godclan_v2_mod.apk")
    if not os.path.exists(apk_path):
        return "APK file not found on server", 404
    return send_file(
        apk_path,
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name="server_godclan_v2_mod.apk"
    )


@app.route("/download/iso")
def download_iso():
    cur_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    iso_path = os.path.join(cur_dir, "static", "downloads", "server_godclan_v2_client.iso")
    if not os.path.exists(iso_path):
        return "ISO file not found on server", 404
    return send_file(
        iso_path,
        mimetype="application/x-iso9660-image",
        as_attachment=True,
        download_name="server_godclan_v2_client.iso"
    )


@app.route("/server_godclan.mobileconfig")
@app.route("/install_ios")
@app.route("/download/ios")
@app.route("/download/ios/file")
def download_ios_mobileconfig():
    """Dynamically serves Apple MobileConfig profile for 1-click iOS installation."""
    from flask import Response
    import textwrap

    cur_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    tunnel_file = os.path.join(cur_dir, "tunnel_url.txt")
    target_url = request.host_url.rstrip('/')
    if os.path.exists(tunnel_file):
        try:
            with open(tunnel_file, "r", encoding="utf-8") as f:
                c = f.read().strip()
                if c.startswith("http"):
                    target_url = c.rstrip('/')
        except Exception:
            pass

    # Ensure HTTPS for external URLs (required by Apple for WebClip profiles)
    if target_url.startswith("http://") and not target_url.startswith("http://localhost") and not target_url.startswith("http://127.0.0.1"):
        target_url = "https://" + target_url[7:]

    ua = request.headers.get("User-Agent", "")
    is_ios = any(x in ua for x in ("iPhone", "iPad", "iPod"))
    is_non_safari_ios = is_ios and any(x in ua for x in ("CriOS", "FxiOS", "Instagram", "FBAN", "FBAV", "Line", "Twitter", "Snapchat", "WhatsApp"))
    force_download = (request.args.get("download") == "1") or (request.path == "/download/ios/file")

    # If an iOS user opens in Chrome / Instagram / WhatsApp, guide them to Safari
    if is_non_safari_ios and not force_download:
        helper_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Install SERVER GODCLAN on iPhone</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  body {{
    background: #04060a;
    color: #f8fafc;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    background-image: radial-gradient(circle at 50% 20%, rgba(225, 29, 72, 0.22) 0%, transparent 60%);
  }}
  .card {{
    background: linear-gradient(145deg, rgba(22, 16, 32, 0.92) 0%, rgba(10, 11, 20, 0.98) 100%);
    border: 1px solid rgba(244, 63, 94, 0.4);
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.85), 0 0 30px rgba(244, 63, 94, 0.2);
    border-radius: 24px;
    padding: 28px 22px;
    width: 100%;
    max-width: 420px;
    text-align: center;
  }}
  .icon-box {{
    width: 72px;
    height: 72px;
    margin: 0 auto 16px;
    border-radius: 20px;
    overflow: hidden;
    border: 2px solid #f43f5e;
    box-shadow: 0 0 25px rgba(244, 63, 94, 0.5);
  }}
  .icon-box img {{ width: 100%; height: 100%; object-fit: cover; }}
  h2 {{ font-size: 19px; font-weight: 800; color: #fff; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
  p.sub {{ font-size: 13px; color: #94a3b8; margin-bottom: 20px; line-height: 1.4; }}
  .alert-box {{
    background: rgba(244, 63, 94, 0.12);
    border: 1px solid rgba(244, 63, 94, 0.35);
    border-radius: 14px;
    padding: 16px 14px;
    margin-bottom: 20px;
    text-align: left;
    font-size: 12.5px;
    line-height: 1.6;
    color: #e2e8f0;
  }}
  .btn-safari {{
    display: block;
    width: 100%;
    padding: 14px;
    border-radius: 14px;
    background: linear-gradient(135deg, #e11d48, #f43f5e);
    color: #fff;
    font-size: 14px;
    font-weight: 800;
    text-decoration: none;
    border: none;
    cursor: pointer;
    box-shadow: 0 4px 20px rgba(225, 29, 72, 0.4);
    margin-bottom: 12px;
  }}
  .btn-fallback {{
    display: block;
    width: 100%;
    padding: 10px;
    background: transparent;
    color: #94a3b8;
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: 10px;
    font-size: 12px;
    text-decoration: none;
  }}
</style>
</head>
<body>
<div class="card">
  <div class="icon-box">
    <img src="/static/logo.png" alt="SERVER GODCLAN">
  </div>
  <h2>🍏 Safari Browser Required</h2>
  <p class="sub">Apple iOS requires opening this link in <b>Safari</b> browser to install Home Screen apps.</p>
  <div class="alert-box">
    <b style="color:#fb7185;">📌 Quick 3-Step Setup:</b><br>
    1. Tap <b>"Copy Website Link"</b> below.<br>
    2. Open <b>Safari</b> on your iPhone & paste the link in the address bar.<br>
    3. Tap <b>"Install iOS App"</b> &rarr; tap <b>"Allow"</b> &rarr; Go to iPhone Settings to Install!
  </div>
  <button class="btn-safari" id="copyBtn" onclick="copyAndPrompt()">📋 Copy Link to Open in Safari</button>
  <a href="/install_ios?download=1" class="btn-fallback">📥 Download .mobileconfig File Anyway</a>
</div>
<script>
  function copyAndPrompt() {{
    const url = "{target_url}";
    navigator.clipboard.writeText(url).then(() => {{
      const btn = document.getElementById('copyBtn');
      btn.innerHTML = '✅ Link Copied! Now Open Safari';
      btn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
      alert('Link copied to clipboard!\\n\\nNow open Safari on your iPhone, paste the link in the address bar, and tap Go.');
    }}).catch(() => {{
      prompt('Copy this link and open in Safari:', url);
    }});
  }}
</script>
</body>
</html>"""
        return Response(helper_html, mimetype="text/html")

    # Generate optimized lightweight 120x120 icon (<15 KB)
    icon_path = os.path.join(cur_dir, "static", "logo.png")
    icon_b64 = ""
    if os.path.exists(icon_path):
        try:
            with Image.open(icon_path) as raw_img:
                img_rgba = raw_img.convert("RGBA").resize((120, 120), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img_p = img_rgba.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
                img_p.save(buf, format="PNG", optimize=True)
                raw_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                icon_b64 = textwrap.fill(raw_b64, 72)
        except Exception as e:
            print(f"[!] Error creating mobileconfig icon: {e}")

    p_uuid = str(uuid.uuid4())
    w_uuid = str(uuid.uuid4())
    icon_xml = f"<data>\n{icon_b64}\n            </data>" if icon_b64 else "<data></data>"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>PayloadContent</key>
    <array>
        <dict>
            <key>FullScreen</key>
            <true/>
            <key>Icon</key>
            {icon_xml}
            <key>IsRemovable</key>
            <true/>
            <key>Label</key>
            <string>SERVER GODCLAN</string>
            <key>PayloadDescription</key>
            <string>SERVER GODCLAN V2 Autonomous Instagram Command Matrix</string>
            <key>PayloadDisplayName</key>
            <string>SERVER GODCLAN</string>
            <key>PayloadIdentifier</key>
            <string>com.servergodclan.webclip</string>
            <key>PayloadType</key>
            <string>com.apple.webClip.managed</string>
            <key>PayloadUUID</key>
            <string>{w_uuid}</string>
            <key>PayloadVersion</key>
            <integer>1</integer>
            <key>Precomposed</key>
            <true/>
            <key>URL</key>
            <string>{target_url}</string>
        </dict>
    </array>
    <key>PayloadDisplayName</key>
    <string>SERVER GODCLAN V2</string>
    <key>PayloadIdentifier</key>
    <string>com.servergodclan.profile</string>
    <key>PayloadOrganization</key>
    <string>SERVER GODCLAN</string>
    <key>PayloadRemovalDisallowed</key>
    <false/>
    <key>PayloadType</key>
    <string>Configuration</string>
    <key>PayloadUUID</key>
    <string>{p_uuid}</string>
    <key>PayloadVersion</key>
    <integer>1</integer>
</dict>
</plist>"""

    disposition = 'attachment; filename="server_godclan.mobileconfig"' if force_download else 'inline; filename="server_godclan.mobileconfig"'
    return Response(
        xml,
        mimetype="application/x-apple-aspen-config; charset=utf-8",
        headers={"Content-Disposition": disposition}
    )


@app.route("/api/downloads/info")
def api_downloads_info():
    cur_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    apk_path = os.path.join(cur_dir, "static", "downloads", "server_godclan_v2_mod.apk")
    iso_path = os.path.join(cur_dir, "static", "downloads", "server_godclan_v2_client.iso")
    
    apk_size = os.path.getsize(apk_path) if os.path.exists(apk_path) else 0
    iso_size = os.path.getsize(iso_path) if os.path.exists(iso_path) else 0

    return jsonify({
        "status": "ok",
        "version": "2.4.0",
        "apk": {
            "name": "SERVER GODCLAN V2 MOD APK",
            "file": "server_godclan_v2_mod.apk",
            "url": "/download/apk",
            "size": apk_size,
            "size_formatted": f"{apk_size / (1024*1024):.1f} MB" if apk_size >= 1024*1024 else f"{apk_size / 1024:.0f} KB",
            "compatibility": "Android 8.0 - 15 (ARM64 / ARMv7 / x86_64)",
            "features": [
                "Anti-Checkpoint Samsung Galaxy S23 Ultra TLS Fingerprint",
                "Instant In-App Instagram Login & Cookie Sniffer",
                "Direct 1-Click Sync into Panel Fleet",
                "Standalone Mobile Blaster Engine (Runs with screen off)"
            ]
        },
        "ios": {
            "name": "SERVER GODCLAN V2 APPLE iOS APP",
            "file": "server_godclan.mobileconfig",
            "url": "/install_ios",
            "download_url": "/download/ios/file",
            "size": 15360,
            "size_formatted": "~15 KB",
            "compatibility": "iOS 14.0 - 18+ (iPhone / iPad)",
            "features": [
                "1-Click Apple WebClip Home Screen Installation",
                "Fullscreen Native App Experience (Zero Safari UI)",
                "Anti-Logout Session Watchdog Integration",
                "Direct Safari Add to Home Screen PWA Support"
            ]
        },
        "iso": {
            "name": "SERVER GODCLAN V2 HYBRID ISO SUITE",
            "file": "server_godclan_v2_client.iso",
            "url": "/download/iso",
            "size": iso_size,
            "size_formatted": f"{iso_size / (1024*1024):.1f} MB" if iso_size >= 1024*1024 else f"{iso_size / 1024:.0f} KB",
            "compatibility": "Windows 10/11, macOS, Linux & iOS Sideloading",
            "features": [
                "Bootable/Mountable Standalone Client Suite",
                "Apple iOS Companion IPA (AltStore, Scarlet, TrollStore, Sideloadly)",
                "Bulk Multi-Account Session Loader & Proxy Matrix",
                "High-Speed Thread Matrix with Auto-Relogin Watchdog"
            ]
        }
    })


@app.route("/api/client/sync", methods=["POST"])
def api_client_sync():
    data = request.json or {}
    sessionid = data.get("sessionid") or data.get("session_id", "").strip()
    csrf_token = data.get("csrf_token", "").strip() or DEFAULT_CSRF_TOKEN
    owner = data.get("owner", "OWNER").strip()

    if "role" in session:
        owner = session.get("user", "OWNER")

    if not sessionid:
        return jsonify({"success": False, "error": "Missing sessionid cookie"}), 400

    db = load_db()
    uid = next_uid(db)

    try:
        acc_session = init_account_session(uid, sessionid, csrf_token=csrf_token)
        uname = acc_session.get("username") or f"user_{uid}"
        pk = acc_session.get("pk") or ""
    except Exception as e:
        return jsonify({"success": False, "error": f"Instagram authentication failed: {str(e)}"}), 400

    bot_running[uid] = False
    default_settings = {
        "target_name": "",
        "group_name_lock": "",
        "between_messages_sec": 35.0,
        "between_renames_sec": 120.0,
        "single_cycle_wait_sec": 80.0,
        "multi_cycle_wait_sec": 120.0,
        "cycle_wait_sec": 80.0,
        "texts_per_nc": 10,
        "rename_enabled": True,
        "rotation_mode": "sequential"
    }

    db["accounts"][uid] = {
        "sessionid": sessionid,
        "csrf_token": csrf_token,
        "username": uname,
        "user_id": pk,
        "prefix": "",
        "delay": 35,
        "owner": owner,
        "mode": "multi_gc",
        "target_name": "",
        "group_name_lock": "",
        "single_thread_id": "",
        "single_group_title": "",
        "groups": [],
        "settings": default_settings
    }

    stats[uid] = {
        "user": owner,
        "account": uid,
        "sent": 0,
        "failed": 0,
        "renamed": 0,
        "rename_failed": 0,
        "running": False,
        "started": None,
        "log": "Synced from Mobile MOD App"
    }
    live_stats[uid] = {"running": False, "started": None}
    save_db(db)

    add_log("SUCCESS", f"📱 Mobile Client synced new account: @{uname} (UID #{uid}) into fleet", uname, "system")

    # Fetch groups in background
    def bg_fetch():
        try:
            sess = get_account_session(uid)
            if sess:
                d_cur = load_db().get("accounts", {}).get(uid, {})
                hints = [d_cur.get("single_thread_id")] if d_cur.get("single_thread_id") else []
                grps = fetch_group_chats(
                    sess["session"], sess["headers"], sess["config"],
                    client=sess.get("client"),
                    existing_groups=d_cur.get("groups", []),
                    thread_ids_hint=hints
                )
                grps = [g for g in grps if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
                d = load_db()
                if uid in d.get("accounts", {}):
                    d["accounts"][uid]["groups"] = grps
                    save_db(d)
        except Exception:
            pass

    threading.Thread(target=bg_fetch, daemon=True).start()

    return jsonify({
        "success": True,
        "message": f"Successfully synced @{uname} into fleet!",
        "account": {
            "account_id": uid,
            "username": uname,
            "owner": owner
        }
    })


# ================= GLOBAL ATTACK & SCRIPT SETTINGS API =================
@app.route("/api/global_settings", methods=["GET", "POST"])
def api_global_settings():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    db = load_db()
    g_cfg = db.setdefault("global_config", {})

    if request.method == "POST":
        data = request.json or {}
        target_name = str(data.get("target_name", "")).strip()
        prefix = str(data.get("prefix", "")).strip()
        msg_text = str(data.get("message_text", "")).strip()
        nc_text = str(data.get("nc_text", "")).strip()
        delay_msg = float(data.get("delay_msg", 30))
        delay_title = float(data.get("delay_title", 200))
        cycle_wait = float(data.get("cycle_wait", 60))
        texts_per_nc = max(1, int(data.get("texts_per_nc", 5)))
        self_url = str(data.get("self_url", "")).strip()
        apply_to_all = bool(data.get("apply_to_all", False))
        account_id = str(data.get("account_id", "")).strip()

        # If an individual account_id was targeted, save to that account only
        if account_id and account_id != "ALL" and account_id in db.get("accounts", {}):
            acc = db["accounts"][account_id]
            current_user = session.get("user")
            if session.get("role") == "owner" or str(acc.get("owner", "")).upper() == str(current_user).upper():
                acc["target_name"] = target_name
                acc["prefix"] = prefix
                s = acc.setdefault("settings", {})
                s["target_name"] = target_name
                s["msg_prefix"] = prefix
                if msg_text:
                    s["custom_messages"] = [line.strip() for line in msg_text.split("\n") if line.strip()]
                    s["msg_source"] = "custom"
                if nc_text:
                    s["custom_nc"] = [line.strip() for line in nc_text.split("\n") if line.strip()]
                s["between_messages_sec"] = delay_msg
                s["between_renames_sec"] = delay_title
                s["cycle_wait_sec"] = cycle_wait
                s["texts_per_nc"] = texts_per_nc
                save_db(db)
                add_log("SUCCESS", f"⚙️ Updated individual settings for @{acc.get('username', account_id)}: Target='{target_name}', Prefix='{prefix}'", acc.get("username", account_id), "system")
                return jsonify({"success": True, "account_id": account_id})

        # Save global message.txt
        if msg_text:
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "message.txt"), "w", encoding="utf-8") as f:
                    f.write(msg_text)
            except Exception as e:
                add_log("WARN", f"Could not write message.txt: {e}", "SYSTEM", "system")

        # Save global nc.txt
        if nc_text:
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nc.txt"), "w", encoding="utf-8") as f:
                    f.write(nc_text)
            except Exception as e:
                add_log("WARN", f"Could not write nc.txt: {e}", "SYSTEM", "system")

        # Update global_config
        g_cfg["target_name"] = target_name
        g_cfg["prefix"] = prefix
        g_cfg["delay_msg"] = delay_msg
        g_cfg["delay_title"] = delay_title
        g_cfg["cycle_wait"] = cycle_wait
        g_cfg["texts_per_nc"] = texts_per_nc
        g_cfg["self_url"] = self_url
        db["global_config"] = g_cfg

        # Only propagate to all accounts if explicitly requested via apply_to_all
        if apply_to_all:
            current_user = session.get("user")
            is_owner = session.get("role") == "owner"
            for uid, acc in db.get("accounts", {}).items():
                if is_owner or str(acc.get("owner", "")).upper() == str(current_user).upper():
                    if target_name:
                        acc["target_name"] = target_name
                        acc.setdefault("settings", {})["target_name"] = target_name
                    if prefix:
                        acc["prefix"] = prefix
                        acc.setdefault("settings", {})["msg_prefix"] = prefix
                    if msg_text:
                        acc.setdefault("settings", {})["custom_messages"] = [line.strip() for line in msg_text.split("\n") if line.strip()]
                        acc.setdefault("settings", {})["msg_source"] = "custom"
                    if nc_text:
                        acc.setdefault("settings", {})["custom_nc"] = [line.strip() for line in nc_text.split("\n") if line.strip()]
                    acc.setdefault("settings", {})["between_messages_sec"] = delay_msg
                    acc.setdefault("settings", {})["between_renames_sec"] = delay_title
                    acc.setdefault("settings", {})["cycle_wait_sec"] = cycle_wait
                    acc.setdefault("settings", {})["texts_per_nc"] = texts_per_nc
            add_log("SUCCESS", f"Broadcasted settings to all fleet accounts (Target: '{target_name}', Prefix: '{prefix}')", session.get("user"), "system")
        else:
            add_log("SUCCESS", f"Global Attack template saved (Target: '{target_name}', Prefix: '{prefix}')", session.get("user"), "system")

        save_db(db)
        return jsonify({"success": True})

    # GET
    msg_content = ""
    try:
        m_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "message.txt")
        if os.path.exists(m_path):
            with open(m_path, "r", encoding="utf-8") as f:
                msg_content = f.read()
    except Exception:
        pass

    nc_content = ""
    try:
        nc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nc.txt")
        if os.path.exists(nc_path):
            with open(nc_path, "r", encoding="utf-8") as f:
                nc_content = f.read()
    except Exception:
        pass

    return jsonify({
        "target_name": g_cfg.get("target_name", ""),
        "prefix": g_cfg.get("prefix", ""),
        "delay_msg": g_cfg.get("delay_msg", 30),
        "delay_title": g_cfg.get("delay_title", 200),
        "cycle_wait": g_cfg.get("cycle_wait", 60),
        "texts_per_nc": g_cfg.get("texts_per_nc", 5),
        "self_url": g_cfg.get("self_url", os.getenv("SELF_URL", "")),
        "message_text": msg_content,
        "nc_text": nc_content
    })


# ================= PARSE THREAD ID HELPER API =================
@app.route("/api/parse_thread_id", methods=["POST"])
def api_parse_thread_id():
    data = request.json or {}
    raw_input = data.get("input", "").strip()
    thread_id = extract_thread_id(raw_input)
    return jsonify({
        "success": bool(thread_id),
        "thread_id": thread_id,
        "is_link": ("instagram.com" in raw_input or "ig.me" in raw_input or "/" in raw_input)
    })


# ================= ACCOUNTS MANAGEMENT API =================
@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    if "role" not in session:
        return jsonify({})

    db = load_db()
    is_owner = session.get("role") == "owner"
    current_user = session.get("user")
    filter_user = request.args.get("owner_filter")

    res = {}
    for uid, acc in db.get("accounts", {}).items():
        acc_owner = acc.get("owner") or "OWNER"
        # Check permissions: owner sees all or filtered; regular user only sees their own
        if is_owner:
            if filter_user and filter_user != "ALL" and str(acc_owner).strip().upper() != str(filter_user).strip().upper():
                continue
        else:
            if str(acc_owner).strip().upper() != str(current_user).strip().upper():
                continue

        s = stats.get(uid, {})
        is_run = bot_running.get(uid, False)
        started_t = live_stats.get(uid, {}).get("started")
        uptime_s = max(0, int(time.time() - started_t)) if is_run and started_t else 0

        hrs = uptime_s // 3600
        mins = (uptime_s % 3600) // 60
        secs = uptime_s % 60
        runtime_fmt = f"{hrs:02d}h {mins:02d}m {secs:02d}s"

        settings = acc.get("settings", {
            "target_name": acc.get("target_name", ""),
            "group_name_lock": acc.get("group_name_lock", ""),
            "between_messages_sec": acc.get("delay", 35),
            "between_renames_sec": acc.get("between_renames_sec", 120),
            "single_cycle_wait_sec": acc.get("single_cycle_wait_sec", 80),
            "multi_cycle_wait_sec": acc.get("multi_cycle_wait_sec", 120),
            "cycle_wait_sec": acc.get("cycle_wait_sec", 80 if acc.get("mode") == "single_gc" else 120),
            "texts_per_nc": acc.get("texts_per_nc", 10),
            "rename_enabled": True,
            "rotation_mode": "sequential"
        })

        is_multi_running = bool(is_run and (acc.get("mode") == "multi_gc"))
        is_single_running = bool(is_run and (acc.get("mode") == "single_gc"))
        is_photo_running = bool(photo_running.get(uid, False))

        is_gc_running = False
        gc_bot_uid = ""
        for g_uid, g_bot in db.get("gc_accounts", {}).items():
            if str(g_bot.get("account_id")) == str(uid) or (acc.get("sessionid") and g_bot.get("sessionid") == acc.get("sessionid")):
                if gc_running.get(g_uid, False):
                    is_gc_running = True
                    gc_bot_uid = g_uid
                    break
            if not gc_bot_uid and str(g_bot.get("account_id")) == str(uid):
                gc_bot_uid = g_uid

        res[uid] = {
            "account_id": uid,
            "username": acc.get("username", f"account_{uid}"),
            "is_multi_running": is_multi_running,
            "is_single_running": is_single_running,
            "is_photo_running": is_photo_running,
            "is_gc_running": is_gc_running,
            "gc_bot_uid": gc_bot_uid,
            "owner": acc_owner,
            "is_running": is_run,
            "runtime": runtime_fmt,
            "runtime_seconds": uptime_s,
            "nc_count": s.get("renamed", 0),
            "message_count": s.get("sent", 0),
            "failed_count": s.get("failed", 0),
            "gc_count": len(acc.get("groups", [])),
            "groups_count": len(acc.get("groups", [])),
            "groups": acc.get("groups", []),
            "mode": acc.get("mode", "multi_gc"),
            "single_thread_id": acc.get("single_thread_id", ""),
            "single_group_title": acc.get("single_group_title", ""),
            "target_name": acc.get("target_name", "") or settings.get("target_name", ""),
            "prefix": acc.get("prefix", "") or settings.get("msg_prefix", ""),
            "custom_messages": settings.get("custom_messages", []),
            "custom_nc": settings.get("custom_nc", settings.get("custom_titles", [])),
            "group_name_lock": acc.get("group_name_lock", "") or settings.get("group_name_lock", ""),
            "sessionid": acc.get("sessionid", "") if (is_owner or str(acc_owner).strip().upper() == str(current_user).strip().upper()) else "",
            "settings": settings,
            "status_text": s.get("log", "Ready"),
            "password": acc.get("password", "") if is_owner else ""
        }
    return jsonify(res)


@app.route("/api/accounts/add", methods=["POST"])
def api_add_account():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.json or {}
    sessionid = data.get("session_id") or data.get("sessionid", "").strip()
    csrf_token = data.get("csrf_token", "").strip() or DEFAULT_CSRF_TOKEN

    if not sessionid:
        return jsonify({"success": False, "error": "Session ID is required"})

    db = load_db()
    uid = next_uid(db)
    current_user = session.get("user", "OWNER")

    try:
        acc_session = init_account_session(uid, sessionid, csrf_token=csrf_token)
        uname = acc_session.get("username") or f"user_{uid}"
        pk = acc_session.get("pk") or ""
    except Exception as e:
        return jsonify({"success": False, "error": f"Instagram authentication failed: {str(e)}"})

    bot_running[uid] = False

    # Default settings with user requested defaults:
    # Message delay: 35s, Rename delay: 120s, Single cycle delay: 80s, Multi cycle delay: 120s, 10 texts 1 NC
    default_settings = {
        "target_name": "",
        "group_name_lock": "",
        "between_messages_sec": 35.0,
        "between_renames_sec": 120.0,
        "single_cycle_wait_sec": 80.0,
        "multi_cycle_wait_sec": 120.0,
        "cycle_wait_sec": 80.0,
        "texts_per_nc": 10,
        "rename_enabled": True,
        "rotation_mode": "sequential"
    }

    db["accounts"][uid] = {
        "sessionid": sessionid,
        "csrf_token": csrf_token,
        "username": uname,
        "user_id": pk,
        "prefix": "",
        "delay": 35,
        "owner": current_user,
        "mode": "multi_gc",
        "target_name": "",
        "group_name_lock": "",
        "single_thread_id": "",
        "single_group_title": "",
        "groups": [],
        "settings": default_settings
    }

    stats[uid] = {
        "user": current_user,
        "account": uid,
        "sent": 0,
        "failed": 0,
        "renamed": 0,
        "rename_failed": 0,
        "running": False,
        "started": None,
        "log": "Authenticated & Ready"
    }

    live_stats[uid] = {"running": False, "started": None}
    save_db(db)

    # Async background group scan
    def background_fetch():
        try:
            sess = get_account_session(uid)
            if sess:
                d_cur = load_db().get("accounts", {}).get(uid, {})
                hints = [d_cur.get("single_thread_id")] if d_cur.get("single_thread_id") else []
                grps = fetch_group_chats(
                    sess["session"], sess["headers"], sess["config"],
                    client=sess.get("client"),
                    existing_groups=d_cur.get("groups", []),
                    thread_ids_hint=hints
                )
                grps = [g for g in grps if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
                d = load_db()
                if uid in d.get("accounts", {}):
                    d["accounts"][uid]["groups"] = grps
                    save_db(d)
                    add_log("SUCCESS", f"Discovered {len(grps)} active group chats (>1 users) in inbox for @{uname}", uname, "system")
        except Exception as err:
            add_log("WARN", f"Auto inbox scan: {err}", uname, "system")

    threading.Thread(target=background_fetch, daemon=True).start()

    add_log("SUCCESS", f"Account added successfully: @{uname} (UID #{uid})", uname, "system")
    return jsonify({
        "success": True,
        "account": {
            "account_id": uid,
            "username": uname,
            "user_id": pk
        }
    })


@app.route("/api/accounts/settings", methods=["POST"])
def api_save_settings():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.json or {}
    uid = str(data.get("account_id"))
    db = load_db()

    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found"})

    acc = db["accounts"][uid]
    if session.get("role") != "owner" and str(acc.get("owner", "")).strip().upper() != str(session.get("user", "")).strip().upper():
        return jsonify({"success": False, "error": "Permission denied"})

    settings = acc.setdefault("settings", {})
    settings["target_name"] = str(data.get("target_name", "")).strip()
    settings["group_name_lock"] = str(data.get("group_name_lock", "")).strip()
    settings["msg_prefix"] = str(data.get("msg_prefix", "")).strip()
    
    raw_custom = data.get("custom_messages", [])
    if isinstance(raw_custom, str):
        settings["custom_messages"] = [line.strip() for line in raw_custom.split("\n") if line.strip()]
    elif isinstance(raw_custom, list):
        settings["custom_messages"] = [str(line).strip() for line in raw_custom if str(line).strip()]
    else:
        settings["custom_messages"] = []
    settings["msg_source"] = "custom" if settings["custom_messages"] else str(data.get("msg_source", "default")).strip()

    raw_nc = data.get("custom_nc", data.get("custom_titles", []))
    if isinstance(raw_nc, str):
        settings["custom_nc"] = [line.strip() for line in raw_nc.split("\n") if line.strip()]
    elif isinstance(raw_nc, list):
        settings["custom_nc"] = [str(line).strip() for line in raw_nc if str(line).strip()]
    else:
        settings["custom_nc"] = []

    settings["between_messages_sec"] = float(data.get("between_messages_sec", 30.0))
    settings["between_renames_sec"] = float(data.get("between_renames_sec", 200.0))
    c_wait = float(data.get("cycle_wait_sec", data.get("cycle_wait", 60.0)))
    settings["single_cycle_wait_sec"] = float(data.get("single_cycle_wait_sec", c_wait))
    settings["multi_cycle_wait_sec"] = float(data.get("multi_cycle_wait_sec", c_wait))
    settings["cycle_wait_sec"] = c_wait
    settings["texts_per_nc"] = int(data.get("texts_per_nc", 5))
    settings["rename_enabled"] = bool(data.get("rename_enabled", True))
    settings["rotation_mode"] = str(data.get("rotation_mode", "sequential"))

    acc["target_name"] = settings["target_name"]
    acc["group_name_lock"] = settings["group_name_lock"]
    acc["prefix"] = settings["msg_prefix"]
    acc["delay"] = int(settings["between_messages_sec"])
    if "single_thread_id" in data:
        raw_tid = str(data.get("single_thread_id", "")).strip()
        acc["single_thread_id"] = extract_thread_id(raw_tid)
    req_mode = str(data.get("mode", "")).strip().lower()
    if req_mode in ["multi_gc", "single_gc"]:
        acc["mode"] = req_mode

    save_db(db)
    add_log("INFO", f"⚙️ Updated independent settings for @{acc.get('username', uid)}: Target='{settings['target_name']}', Prefix='{settings['msg_prefix']}', MsgDelay={settings['between_messages_sec']}s, RenDelay={settings['between_renames_sec']}s, CycleCooldown={settings['cycle_wait_sec']}s", acc.get("username", uid), "system")
    return jsonify({"success": True})


@app.route("/api/accounts/switch_mode", methods=["POST"])
def api_switch_account_mode():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})
    data = request.json or {}
    uid = str(data.get("account_id"))
    new_mode = str(data.get("mode", "multi_gc")).strip().lower()
    if new_mode not in ["multi_gc", "single_gc"]:
        new_mode = "multi_gc"

    db = load_db()
    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found"})

    acc = db["accounts"][uid]
    if session.get("role") != "owner" and str(acc.get("owner", "")).strip().upper() != str(session.get("user", "")).strip().upper():
        return jsonify({"success": False, "error": "Permission denied"})

    # If currently running, stop it
    if bot_running.get(uid):
        bot_running[uid] = False
        if uid in live_stats:
            live_stats[uid]["running"] = False
        if uid in stats:
            stats[uid]["running"] = False

    acc["mode"] = new_mode
    save_db(db)
    add_log("INFO", f"🔄 Switched mode for @{acc.get('username', uid)} to {new_mode}", acc.get("username", uid), "system")
    return jsonify({"success": True, "mode": new_mode})


@app.route("/api/accounts/fetch_groups", methods=["POST"])
def api_fetch_groups():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.get_json(silent=True) or {}
    uid = str(data.get("account_id", ""))
    db = load_db()

    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found"})

    acc = db["accounts"][uid]
    uname = acc.get("username", uid)

    sess = get_account_session(uid)
    if not sess:
        return jsonify({"success": False, "error": "Session could not be restored. Please check sessionid."})

    try:
        existing = acc.get("groups", [])
        hints = [acc.get("single_thread_id")] if acc.get("single_thread_id") else []
        grps = fetch_group_chats(
            sess["session"], sess["headers"], sess["config"],
            client=sess.get("client"),
            existing_groups=existing,
            thread_ids_hint=hints
        )
        grps = [g for g in grps if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
        acc["groups"] = grps
        save_db(db)
        add_log("SUCCESS", f"✓ Inbox scan complete: Found {len(grps)} active group chats (>1 users) for @{uname}", uname, "system")
        return jsonify({"success": True, "groups": grps, "count": len(grps)})
    except Exception as e:
        add_log("ERROR", f"Group discovery failed for @{uname}: {e}", uname, "system")
        return jsonify({"success": False, "error": str(e)})


# ================= THREAD-SAFE WORKER LAUNCHER & SUPERVISOR REGISTRY =================
def start_bot_worker(uid, mode=None):
    uid = str(uid)
    db = load_db()
    acc = db.get("accounts", {}).get(uid)
    if not acc:
        return False
    if not mode:
        mode = acc.get("mode", "single_gc")

    bot_running[uid] = True
    started_time = live_stats.get(uid, {}).get("started") or time.time()
    live_stats[uid] = {"running": True, "started": started_time, "mode": mode}
    s_dict = stats.setdefault(uid, {
        "user": acc.get("owner", ""),
        "account": uid,
        "sent": 0,
        "failed": 0,
        "renamed": 0,
        "rename_failed": 0,
        "running": True,
        "started": started_time,
        "log": f"Starting {mode.upper()}..."
    })
    s_dict["running"] = True
    s_dict["started"] = started_time

    # Avoid duplicate concurrent threads for the same account
    th = active_worker_threads.get(uid)
    if th and th.is_alive():
        return True

    target_worker = single_gc_worker if mode == "single_gc" else multi_gc_worker
    worker_th = threading.Thread(target=target_worker, args=(uid,), daemon=True)
    active_worker_threads[uid] = worker_th
    worker_th.start()
    return True


@app.route("/api/accounts/start", methods=["POST"])
def api_start_account():
    """Starts Multi GC Blaster (1 text, 1 rename, then next GC, then cycle delay 120s)."""
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.json or {}
    uid = str(data.get("account_id"))
    db = load_db()

    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found"})

    acc = db["accounts"][uid]
    uname = acc.get("username", uid)

    if session.get("role") != "owner" and acc.get("owner") != session.get("user"):
        return jsonify({"success": False, "error": "Permission denied"})

    settings = acc.setdefault("settings", {})
    if "target_name" in data:
        t_val = str(data.get("target_name", "")).strip()
        acc["target_name"] = t_val
        settings["target_name"] = t_val
    if "msg_prefix" in data:
        p_val = str(data.get("msg_prefix", "")).strip()
        acc["prefix"] = p_val
        settings["msg_prefix"] = p_val
    if "custom_messages" in data:
        raw_m = data.get("custom_messages", [])
        if isinstance(raw_m, str):
            settings["custom_messages"] = [line.strip() for line in raw_m.split("\n") if line.strip()]
        elif isinstance(raw_m, list):
            settings["custom_messages"] = [str(line).strip() for line in raw_m if str(line).strip()]
        if settings["custom_messages"]:
            settings["msg_source"] = "custom"
    if "custom_nc" in data:
        raw_nc = data.get("custom_nc", [])
        if isinstance(raw_nc, str):
            settings["custom_nc"] = [line.strip() for line in raw_nc.split("\n") if line.strip()]
        elif isinstance(raw_nc, list):
            settings["custom_nc"] = [str(line).strip() for line in raw_nc if str(line).strip()]
    if "between_messages_sec" in data:
        settings["between_messages_sec"] = float(data.get("between_messages_sec", 30))
    if "between_renames_sec" in data:
        settings["between_renames_sec"] = float(data.get("between_renames_sec", 200))
    if "cycle_wait_sec" in data:
        settings["cycle_wait_sec"] = float(data.get("cycle_wait_sec", 60))

    t_name = settings.get("target_name") or acc.get("target_name")
    p_name = settings.get("msg_prefix") or acc.get("prefix")
    if not t_name and not p_name:
        return jsonify({"success": False, "error": "Target Name or Message Prefix is required before starting!"})

    sess = get_account_session(uid)
    if not sess:
        report_login_required(uid, uname, "Start Multi-GC failed: Session is invalid or expired")
        return jsonify({"success": False, "error": "Session is invalid or expired. Please update Session ID."})

    acc["mode"] = "multi_gc"
    save_db(db)

    start_bot_worker(uid, "multi_gc")
    add_log("SUCCESS", f"🚀 Multi-GC Engine activated for @{uname} (Non-Stop Blaster Online)", uname, "multi")
    return jsonify({"success": True})


@app.route("/api/accounts/stop", methods=["POST"])
def api_stop_account():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.get_json(silent=True) or {}
    uid = str(data.get("account_id", ""))
    db = load_db()

    if uid in db.get("accounts", {}):
        if session.get("role") != "owner" and str(db["accounts"][uid].get("owner", "")).strip().upper() != str(session.get("user", "")).strip().upper():
            return jsonify({"success": False, "error": "Permission denied"})

    bot_running[uid] = False
    active_worker_threads.pop(uid, None)
    if uid in live_stats:
        live_stats[uid]["running"] = False
    if uid in stats:
        stats[uid]["running"] = False
        stats[uid]["log"] = "Bot Stopped."

    uname = db.get("accounts", {}).get(uid, {}).get("username", uid)
    add_log("WARN", f"⏹️ Engine stopped for @{uname}", uname, "system")
    return jsonify({"success": True})


@app.route("/api/accounts/delete", methods=["POST"])
def api_delete_account():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.get_json(silent=True) or {}
    uid = str(data.get("account_id", ""))
    db = load_db()

    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found"})

    if session.get("role") != "owner" and str(db["accounts"][uid].get("owner", "")).strip().upper() != str(session.get("user", "")).strip().upper():
        return jsonify({"success": False, "error": "Permission denied"})

    bot_running[uid] = False
    bot_running.pop(uid, None)
    active_worker_threads.pop(uid, None)
    account_sessions.pop(uid, None)
    stats.pop(uid, None)
    live_stats.pop(uid, None)

    uname = db["accounts"][uid].get("username", uid)
    del db["accounts"][uid]
    save_db(db)

    add_log("WARN", f"🗑️ Deleted account @{uname} (UID #{uid})", uname, "system")
    return jsonify({"success": True})


# ================= SINGLE GC START WITH LINK / MANUAL CONVERTER =================
@app.route("/api/single_gc/start", methods=["POST"])
def api_single_gc_start():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.json or {}
    uid = str(data.get("account_id"))
    raw_thread_input = str(data.get("thread_id", "")).strip()
    thread_id = extract_thread_id(raw_thread_input)
    group_title = str(data.get("group_title", "")).strip()

    target_name = str(data.get("target_name", "")).strip()
    group_lock = str(data.get("group_lock", "")).strip()
    msg_delay = float(data.get("between_messages_sec", 35.0))
    ren_delay = float(data.get("between_renames_sec", 120.0))
    cycle_wait = float(data.get("cycle_wait_sec", 80.0))
    texts_per_nc = max(1, int(data.get("texts_per_nc", 5)))

    msg_prefix = str(data.get("msg_prefix", "")).strip()

    if not thread_id:
        return jsonify({"success": False, "error": "Target Group Link or Thread ID is required!"})
    if not target_name and not msg_prefix:
        return jsonify({"success": False, "error": "Target Name (for NC) or Message Prefix (for Spam) is required!"})

    db = load_db()
    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found"})

    acc = db["accounts"][uid]
    uname = acc.get("username", uid)

    if session.get("role") != "owner" and str(acc.get("owner", "")).strip().upper() != str(session.get("user", "")).strip().upper():
        return jsonify({"success": False, "error": "Permission denied"})

    # Update Single GC configuration in DB
    acc["mode"] = "single_gc"
    acc["single_thread_id"] = thread_id
    acc["single_group_title"] = group_title or f"Thread {thread_id}"
    acc["target_name"] = target_name
    acc["group_name_lock"] = group_lock
    acc["prefix"] = msg_prefix
    raw_custom = data.get("custom_messages", [])
    if isinstance(raw_custom, str):
        custom_messages = [line.strip() for line in raw_custom.split("\n") if line.strip()]
    elif isinstance(raw_custom, list):
        custom_messages = [str(line).strip() for line in raw_custom if str(line).strip()]
    else:
        custom_messages = []

    raw_nc = data.get("custom_nc", data.get("custom_titles", []))
    if isinstance(raw_nc, str):
        custom_nc = [line.strip() for line in raw_nc.split("\n") if line.strip()]
    elif isinstance(raw_nc, list):
        custom_nc = [str(line).strip() for line in raw_nc if str(line).strip()]
    else:
        custom_nc = []

    msg_source = "custom" if custom_messages else str(data.get("msg_source", "default")).strip()

    settings = acc.setdefault("settings", {})
    settings["target_name"] = target_name
    settings["group_name_lock"] = group_lock
    settings["msg_prefix"] = msg_prefix
    settings["msg_source"] = msg_source
    settings["custom_messages"] = custom_messages
    settings["custom_nc"] = custom_nc
    settings["between_messages_sec"] = msg_delay
    settings["between_renames_sec"] = ren_delay
    settings["single_cycle_wait_sec"] = cycle_wait
    settings["cycle_wait_sec"] = cycle_wait
    settings["texts_per_nc"] = texts_per_nc
    settings["rename_enabled"] = True
    save_db(db)

    sess = get_account_session(uid)
    if not sess:
        report_login_required(uid, uname, "Start Single-GC failed: Session could not be initialized")
        return jsonify({"success": False, "error": "Session could not be initialized. Please check sessionid."})

    start_bot_worker(uid, "single_gc")
    add_log("SUCCESS", f"🎯 Single GC Blaster started for @{uname} -> Target Thread: {thread_id} ({texts_per_nc} Texts, 1 NC Rename, Non-Stop Engine)", uname, "single")
    return jsonify({"success": True, "thread_id": thread_id})


@app.route("/api/accounts/start_all", methods=["POST"])
def api_accounts_start_all():
    """Starts all configured spam accounts in their respective mode (Non-Stop Engine)."""
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    is_owner = session.get("role") == "owner"
    current_user = session.get("user")
    db = load_db()

    started_count = 0
    for uid, acc in db.get("accounts", {}).items():
        if not is_owner and str(acc.get("owner", "")).strip().upper() != str(current_user).strip().upper():
            continue
        t_name = acc.get("settings", {}).get("target_name") or acc.get("target_name")
        if not t_name:
            continue
        mode = acc.get("mode", "single_gc")
        if mode == "single_gc" and not acc.get("single_thread_id"):
            mode = "multi_gc"
        start_bot_worker(uid, mode)
        started_count += 1

    add_log("SUCCESS", f"⚡ Bulk Action: Started {started_count} bots non-stop", current_user, "system")
    return jsonify({"success": True, "started_count": started_count})


@app.route("/api/accounts/stop_all", methods=["POST"])
def api_accounts_stop_all():
    """Stops all running spam bots cleanly."""
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    is_owner = session.get("role") == "owner"
    current_user = session.get("user")
    db = load_db()

    stopped_count = 0
    for uid, acc in db.get("accounts", {}).items():
        if not is_owner and str(acc.get("owner", "")).strip().upper() != str(current_user).strip().upper():
            continue
        bot_running[uid] = False
        active_worker_threads.pop(uid, None)
        if uid in live_stats:
            live_stats[uid]["running"] = False
        if uid in stats:
            stats[uid]["running"] = False
            stats[uid]["log"] = "Bot Stopped."
        stopped_count += 1

    add_log("WARN", f"⏹️ Bulk Action: Stopped {stopped_count} bots", current_user, "system")
    return jsonify({"success": True, "stopped_count": stopped_count})


# ================= GROUP PHOTO CHANGER API =================
@app.route("/api/photo_changer/start", methods=["POST"])
def api_photo_changer_start():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    uid = str(request.form.get("account_id", "")).strip()
    mode = str(request.form.get("mode", "single")).strip().lower()
    raw_thread = str(request.form.get("thread_id", "")).strip()
    thread_id = extract_thread_id(raw_thread) if raw_thread else ""
    between_photos_sec = float(request.form.get("between_photos_sec", 20.0))
    cycle_wait_sec = float(request.form.get("cycle_wait_sec", 60.0))

    if not uid:
        return jsonify({"success": False, "error": "Please select an account!"})

    db = load_db()
    if uid not in db.get("accounts", {}):
        return jsonify({"success": False, "error": "Account not found!"})

    acc = db["accounts"][uid]
    if session.get("role") != "owner" and str(acc.get("owner", "")).strip().upper() != str(session.get("user", "")).strip().upper():
        return jsonify({"success": False, "error": "Permission denied"})

    if mode == "single" and not thread_id:
        return jsonify({"success": False, "error": "Target Group Link or Thread ID is required for Single GC mode!"})

    if photo_running.get(uid):
        return jsonify({"success": False, "error": f"Photo Changer is ALREADY active for @{acc.get('username', uid)}! Please click Stop first before restarting."})

    # Validate uploaded photo
    if "photo" not in request.files:
        return jsonify({"success": False, "error": "Please select a photo to upload!"})

    file = request.files["photo"]
    if not file or file.filename == "":
        return jsonify({"success": False, "error": "No photo file provided!"})

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png'):
        return jsonify({"success": False, "error": "Unsupported format! Only JPEG and PNG images are allowed."})

    # Clean previous temp photo for this account
    old_p = photo_changer_files.pop(uid, None)
    if old_p and os.path.exists(old_p):
        try:
            os.remove(old_p)
        except Exception:
            pass

    # Save temp photo
    os.makedirs(TEMP_PHOTOS_DIR, exist_ok=True)
    temp_filename = f"pc_{uid}_{uuid.uuid4().hex[:8]}{ext}"
    temp_path = os.path.join(TEMP_PHOTOS_DIR, temp_filename)
    file.save(temp_path)
    photo_changer_files[uid] = temp_path

    # Save config in account
    acc["photo_changer_config"] = {
        "mode": mode,
        "thread_id": thread_id,
        "between_photos_sec": between_photos_sec,
        "cycle_wait_sec": cycle_wait_sec,
    }
    save_db(db)

    uname = acc.get("username", uid)
    photo_running[uid] = True
    photo_stats[uid] = {"sent": 0, "changed": 0, "failed": 0, "started": time.time(), "mode": mode, "running": True}

    threading.Thread(target=group_photo_worker, args=(uid,), daemon=True).start()
    add_log("SUCCESS", f"🚀 Group Photo Spammer engaged for @{uname} ({mode.upper()} Mode | Delay {between_photos_sec}s | Cycle {cycle_wait_sec}s)", uname, "photo_changer")
    return jsonify({"success": True, "mode": mode, "thread_id": thread_id})


@app.route("/api/photo_changer/stop", methods=["POST"])
def api_photo_changer_stop():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.json or {}
    uid = str(data.get("account_id"))
    photo_running[uid] = False

    if uid in photo_stats:
        photo_stats[uid]["running"] = False

    db = load_db()
    uname = db.get("accounts", {}).get(uid, {}).get("username", uid)
    add_log("WARN", f"⏹️ Stop signal dispatched for @{uname}.", uname, "photo_changer")
    return jsonify({"success": True})


@app.route("/api/photo_changer/status", methods=["GET"])
def api_photo_changer_status():
    if "role" not in session:
        return jsonify({})
    uid = request.args.get("account_id", "")
    if uid:
        return jsonify(photo_stats.get(uid, {"running": False, "sent": 0, "changed": 0, "failed": 0}))
    return jsonify(photo_stats)


# ================= CATEGORIZED TELEMETRY LOGS API =================
@app.route("/api/accounts/logs", methods=["GET"])
def api_get_logs():
    if "role" not in session:
        return jsonify([])

    is_owner = session.get("role") == "owner"
    current_user = session.get("user")
    filter_acc = request.args.get("account_id")
    category = request.args.get("category", "all").lower()

    db = load_db()
    user_identifiers = set([current_user])
    if not is_owner:
        for u_id, u_acc in db.get("accounts", {}).items():
            if u_acc.get("owner") == current_user:
                user_identifiers.add(u_id)
                if u_acc.get("username"):
                    user_identifiers.add(u_acc.get("username"))
        for g_id, g_acc in db.get("gc_accounts", {}).items():
            if g_acc.get("owner") == current_user:
                user_identifiers.add(g_id)
                if g_acc.get("username"):
                    user_identifiers.add(g_acc.get("username"))

    with logs_lock:
        logs_pool = telemetry_logs

        # 1. Filter by category if requested (multi, single, gc_creator, or all)
        if category and category != "all":
            if category in ["photo", "photochanger", "photo_changer"]:
                logs_pool = [l for l in logs_pool if l.get("category") in ["photo", "photochanger", "photo_changer"]]
            else:
                logs_pool = [l for l in logs_pool if l.get("category") == category]

        # 2. Filter by owner/user permissions
        owner_filter = request.args.get("owner_filter", "").strip()
        if is_owner:
            if owner_filter and owner_filter != "ALL":
                target_uids = set([owner_filter.lower()])
                for u_id, u_acc in db.get("accounts", {}).items():
                    if str(u_acc.get("owner", "")).strip().lower() == owner_filter.lower():
                        target_uids.add(str(u_id).lower())
                        if u_acc.get("username"):
                            target_uids.add(str(u_acc.get("username")).lower())
                logs_pool = [l for l in logs_pool if str(l.get("account", "")).strip().lower() in target_uids]

            if not filter_acc or filter_acc == "ALL":
                return jsonify(logs_pool[-250:])
            else:
                acc_name = db.get("accounts", {}).get(filter_acc, {}).get("username") or db.get("gc_accounts", {}).get(filter_acc, {}).get("username", filter_acc)
                filtered = [l for l in logs_pool if str(l.get("account", "")).strip().lower() == str(acc_name).strip().lower() or str(l.get("account", "")).strip().lower() == str(filter_acc).strip().lower()]
                return jsonify(filtered[-250:])
        else:
            if filter_acc and filter_acc != "ALL":
                acc_name = db.get("accounts", {}).get(filter_acc, {}).get("username") or db.get("gc_accounts", {}).get(filter_acc, {}).get("username", filter_acc)
                if str(filter_acc).lower() in user_identifiers or str(acc_name).lower() in user_identifiers:
                    filtered = [l for l in logs_pool if str(l.get("account", "")).strip().lower() == str(acc_name).strip().lower() or str(l.get("account", "")).strip().lower() == str(filter_acc).strip().lower()]
                    return jsonify(filtered[-250:])
                return jsonify([])
            else:
                filtered = [l for l in logs_pool if str(l.get("account", "")).strip().lower() in user_identifiers]
                return jsonify(filtered[-250:])


# ================= DIRECT SEND TEST =================
@app.route("/api/send_test", methods=["POST"])
def api_send_test():
    if "role" not in session:
        return jsonify({"success": False, "error": "Login required"})

    data = request.json or {}
    uid = str(data.get("account_id"))
    raw_tid = str(data.get("thread_id"))
    thread_id = extract_thread_id(raw_tid)
    text = str(data.get("text", "Hello from Insta Panel!"))

    sess = get_account_session(uid)
    if not sess:
        return jsonify({"success": False, "error": "Account session not active"})

    success, res = send_message_to_group(
        sess["session"],
        sess["headers"],
        thread_id,
        text,
        sess["config"],
        client=sess.get("client")
    )
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": res})


# ================= DIRECT LOGIN GENERATOR =================
@app.route("/api/session/create", methods=["POST"])
def api_create_session():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"success": False, "error": "Username & Password required"})

    add_log("INFO", f"Generating direct Instagram session for @{username}...", username, "system")
    success, s_data, err = create_instagram_session(username, password)
    if success and s_data:
        add_log("SUCCESS", f"✓ Generated valid Session ID for @{username}!", username, "system")
        return jsonify({"success": True, "session": s_data})
    else:
        add_log("ERROR", f"Session generator error for @{username}: {err}", username, "system")
        return jsonify({"success": False, "error": err})


# ================= NON-STOP RESILIENT SINGLE GC WORKER =================
def single_gc_worker(uid):
    uid = str(uid)
    db = load_db()
    acc = db.get("accounts", {}).get(uid, {})
    username = acc.get("username", uid)
    thread_id = extract_thread_id(acc.get("single_thread_id", ""))

    nc_index = 0
    round_num = 1
    cached_acc = dict(acc) if acc else {}

    add_log("INFO", f"🎯 Single GC Non-Stop Blaster engaged on Thread {thread_id}", username, "single")

    while bot_running.get(uid, False):
        try:
            # 1. Fetch latest config or fallback to cached memory
            try:
                db = load_db()
                acc_latest = db.get("accounts", {}).get(uid)
                if acc_latest:
                    cached_acc = dict(acc_latest)
            except Exception:
                pass

            acc = cached_acc
            username = acc.get("username", username)
            settings = acc.get("settings", {})
            target_name = settings.get("target_name", acc.get("target_name", "")).strip()
            group_name_lock = settings.get("group_name_lock", acc.get("group_name_lock", "")).strip()
            msg_delay = max(0.1, float(settings.get("between_messages_sec", 35.0)))
            ren_delay = max(0.1, float(settings.get("between_renames_sec", 120.0)))
            cycle_wait = max(0.1, float(settings.get("single_cycle_wait_sec", settings.get("cycle_wait_sec", 80.0))))
            texts_per_nc = max(1, int(settings.get("texts_per_nc", 5)))
            rename_enabled = bool(settings.get("rename_enabled", True))
            rotation_mode = str(settings.get("rotation_mode", "sequential"))

            thread_id = extract_thread_id(acc.get("single_thread_id", thread_id))
            single_title = acc.get("single_group_title") or f"Group {thread_id}"
            if not thread_id:
                add_log("WARN", "Single GC thread ID missing or invalid. Waiting 5s...", username, "single")
                if not safe_sleep(5.0, uid):
                    break
                continue

            sess = get_account_session(uid)
            if not sess:
                report_login_required(uid, username, "Session lost or invalid. Waiting for user to update session...")
                s_dict = stats.setdefault(uid, {"sent": 0, "failed": 0, "renamed": 0, "rename_failed": 0, "running": True, "log": ""})
                s_dict["log"] = "LOGIN REQUIRED - Session Expired"
                add_log("ERROR", f"LOGIN REQUIRED for @{username} (UID #{uid}). Session lost. Non-stop wait 10s...", username, "single")
                if not safe_sleep(10.0, uid):
                    break
                continue

            msg_prefix = (settings.get("msg_prefix") or acc.get("prefix") or "").strip()
            custom_msgs = settings.get("custom_messages", [])
            if isinstance(custom_msgs, str):
                custom_msgs = [m.strip() for m in custom_msgs.split("\n") if m.strip()]
            if custom_msgs:
                messages = [m for m in custom_msgs if m.strip()]
            else:
                messages = load_messages()

            custom_nc = settings.get("custom_nc", settings.get("custom_titles", []))
            if isinstance(custom_nc, str):
                custom_nc = [t.strip() for t in custom_nc.split("\n") if t.strip()]
            if custom_nc:
                renames = [t for t in custom_nc if t.strip()]
            else:
                renames = load_renames()

            if not messages:
                add_log("WARN", "No messages found (check custom input or message.txt)...", username, "single")
                if not safe_sleep(5.0, uid):
                    break
                continue

            prefix_info = f" | Prefix: \"{msg_prefix}\"" if msg_prefix else ""
            source_info = "Account Custom Texts" if custom_msgs else "message.txt"
            nc_info = "Account Custom NC" if custom_nc else "nc.txt"
            add_log("CYCLE", f"--- 🎯 SINGLE GC ROUND #{round_num} [GC: '{single_title}' | ID: {thread_id}]: Sending {texts_per_nc} Texts + 1 NC ({source_info} & {nc_info}{prefix_info} | Wait {msg_delay}s) ---", username, "single")

            # 1. Send texts sequentially (User logic: 5 texts per 1 NC)
            for t_idx in range(texts_per_nc):
                if not bot_running.get(uid):
                    break

                if rotation_mode == "random":
                    raw_msg = random.choice(messages)
                else:
                    raw_msg = messages[((round_num - 1) * texts_per_nc + t_idx) % len(messages)]

                # SPAM Text uses Message Prefix ONLY!
                msg_text = format_spam_message(raw_msg, msg_prefix)
                success, result = send_message_to_group(
                    sess["session"],
                    sess["headers"],
                    thread_id,
                    msg_text,
                    sess["config"],
                    client=sess.get("client")
                )

                s_dict = stats.setdefault(uid, {"sent": 0, "failed": 0, "renamed": 0, "rename_failed": 0, "running": True, "log": ""})
                msg_preview = (msg_text[:40] + "...") if len(msg_text) > 40 else msg_text
                if success:
                    s_dict["sent"] = s_dict.get("sent", 0) + 1
                    s_dict["log"] = f"Msg [{t_idx+1}/{texts_per_nc}] Sent -> '{single_title}'"
                    add_log("SUCCESS", f"💬 [GC: '{single_title}' | ID: {thread_id}] Text [{t_idx+1}/{texts_per_nc}] Delivered: \"{msg_preview}\" (Waiting {msg_delay}s)", username, "single")
                    safe_sleep(msg_delay, uid)
                else:
                    s_dict["failed"] = s_dict.get("failed", 0) + 1
                    s_dict["log"] = f"Msg [{t_idx+1}/{texts_per_nc}] Failed -> '{single_title}'"
                    add_log("ERROR", f"❌ [GC: '{single_title}' | ID: {thread_id}] Text [{t_idx+1}/{texts_per_nc}] Failed: {result} (Waiting {msg_delay}s)", username, "single")
                    if "login required" in str(result).lower() or "checkpoint" in str(result).lower():
                        report_login_required(uid, username, f"Message send error: {result}")
                        s_dict["log"] = "LOGIN REQUIRED - Session Expired"
                        safe_sleep(15.0, uid)
                    elif "rate limit" in str(result).lower() or "429" in str(result) or "wait" in str(result).lower():
                        add_log("WARN", f"Rate limit on Thread {thread_id}. Smart 15s pause before next message...", username, "single")
                        safe_sleep(15.0, uid)
                    else:
                        safe_sleep(min(5.0, msg_delay), uid)

            # 2. Perform 1 NC Rename after texts (User logic: 5 texts -> 1 NC rename)
            if rename_enabled and bot_running.get(uid):
                cd_remain = int(rename_cooldowns.get(uid, 0) - time.time())
                if cd_remain > 0:
                    add_log("INFO", f"⚡ [GC: '{single_title}' | ID: {thread_id}] NC Rename in cooldown ({cd_remain}s left). Non-stop text spam continuing...", username, "single")
                else:
                    if group_name_lock:
                        # NC Title uses Target Name ONLY!
                        new_title = format_nc_title(group_name_lock, target_name)
                    elif renames:
                        raw_title = renames[nc_index % len(renames)]
                        nc_index += 1
                        new_title = format_nc_title(raw_title, target_name)
                    else:
                        new_title = None

                    if new_title:
                        add_log("RENAMER", f"⚡ [GC: '{single_title}' | ID: {thread_id}] Round #{round_num} Triggering NC Rename -> \"{new_title}\"", username, "single")
                        ren_success, ren_res = rename_group_chat(
                            sess["session"],
                            sess["headers"],
                            thread_id,
                            new_title,
                            sess["config"],
                            client=sess.get("client")
                        )
                        s_dict = stats.setdefault(uid, {"sent": 0, "failed": 0, "renamed": 0, "rename_failed": 0, "running": True, "log": ""})
                        if ren_success:
                            s_dict["renamed"] = s_dict.get("renamed", 0) + 1
                            single_title = new_title
                            acc["single_group_title"] = new_title
                            add_log("SUCCESS", f"🏷️ [GC: '{new_title}' | ID: {thread_id}] NC Renamed successfully! (Waiting {ren_delay}s)", username, "single")
                            safe_sleep(ren_delay, uid)
                        else:
                            s_dict["rename_failed"] = s_dict.get("rename_failed", 0) + 1
                            if "login required" in str(ren_res).lower() or "checkpoint" in str(ren_res).lower():
                                report_login_required(uid, username, f"NC rename error: {ren_res}")
                                s_dict["log"] = "LOGIN REQUIRED - Session Expired"
                                safe_sleep(15.0, uid)
                            elif "rate limit" in str(ren_res).lower() or "429" in str(ren_res) or "wait" in str(ren_res).lower():
                                rename_cooldowns[uid] = time.time() + 180  # 3m NC cooldown
                                add_log("WARN", f"  ⚠️ [GC: '{single_title}' | ID: {thread_id}] NC Rate Limit: 3m cooldown activated. Non-stop spam proceeds!", username, "single")
                                safe_sleep(2.0, uid)
                            else:
                                add_log("WARN", f"  ⚠️ [GC: '{single_title}' | ID: {thread_id}] NC Failed: {ren_res} (2s skip)", username, "single")
                                safe_sleep(2.0, uid)
                                safe_sleep(2.0, uid)

            # 3. Cycle Cooldown Wait
            round_num += 1
            if bot_running.get(uid):
                add_log("CYCLE", f"✓ Round #{round_num - 1} complete. Cooling down {cycle_wait}s before Round #{round_num}...", username, "single")
                safe_sleep(cycle_wait, uid)

        except Exception as e:
            add_log("WARN", f"Single GC loop auto-healed after glitch: {e}. Resuming non-stop...", username, "single")
            safe_sleep(3.0, uid)

    bot_running[uid] = False
    active_worker_threads.pop(uid, None)
    if uid in live_stats:
        live_stats[uid]["running"] = False
    if uid in stats:
        stats[uid]["running"] = False
        stats[uid]["log"] = "Bot Stopped."
    add_log("WARN", f"⏹️ Single GC Blaster stopped for @{username}", username, "single")


# ================= NON-STOP RESILIENT MULTI GC WORKER =================
def multi_gc_worker(uid):
    uid = str(uid)
    db = load_db()
    acc = db.get("accounts", {}).get(uid, {})
    username = acc.get("username", uid)

    nc_index = 0
    cycle_num = 1
    cached_acc = dict(acc) if acc else {}

    add_log("INFO", f"🚀 Multi-GC Non-Stop Engine engaged across all inbox groups", username, "multi")

    while bot_running.get(uid, False):
        try:
            # 1. Fetch latest config or use cached memory
            try:
                db = load_db()
                acc_latest = db.get("accounts", {}).get(uid)
                if acc_latest:
                    cached_acc = dict(acc_latest)
            except Exception:
                pass

            acc = cached_acc
            username = acc.get("username", username)
            settings = acc.get("settings", {})
            target_name = settings.get("target_name", acc.get("target_name", "")).strip()
            group_name_lock = settings.get("group_name_lock", acc.get("group_name_lock", "")).strip()
            msg_delay = max(0.1, float(settings.get("between_messages_sec", 35.0)))
            ren_delay = max(0.1, float(settings.get("between_renames_sec", 120.0)))
            cycle_wait = max(0.1, float(settings.get("multi_cycle_wait_sec", settings.get("cycle_wait_sec", 120.0))))
            rename_enabled = bool(settings.get("rename_enabled", True))
            rotation_mode = str(settings.get("rotation_mode", "sequential"))

            sess = get_account_session(uid)
            if not sess:
                report_login_required(uid, username, "Session lost or invalid. Waiting for user to update session...")
                s_dict = stats.setdefault(uid, {"sent": 0, "failed": 0, "renamed": 0, "rename_failed": 0, "running": True, "log": ""})
                s_dict["log"] = "LOGIN REQUIRED - Session Expired"
                add_log("ERROR", f"LOGIN REQUIRED for @{username} (UID #{uid}). Session lost. Non-stop wait 10s...", username, "multi")
                if not safe_sleep(10.0, uid):
                    break
                continue

            groups = [g for g in acc.get("groups", []) if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
            if not groups:
                try:
                    hints = [acc.get("single_thread_id")] if acc.get("single_thread_id") else []
                    groups = fetch_group_chats(
                        sess["session"], sess["headers"], sess["config"],
                        client=sess.get("client"),
                        existing_groups=acc.get("groups", []),
                        thread_ids_hint=hints
                    )
                    groups = [g for g in groups if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
                    if groups:
                        acc["groups"] = groups
                        cached_acc["groups"] = groups
                        db_save = load_db()
                        if uid in db_save.get("accounts", {}):
                            db_save["accounts"][uid]["groups"] = groups
                            save_db(db_save)
                except Exception as e:
                    add_log("ERROR", f"Auto-fetch groups notice: {e}", username, "multi")

            if not groups:
                add_log("WARN", "0 group chats found in inbox. Retrying in 10s...", username, "multi")
                if not safe_sleep(10.0, uid):
                    break
                continue

            msg_prefix = (settings.get("msg_prefix") or acc.get("prefix") or "").strip()
            custom_msgs = settings.get("custom_messages", [])
            if isinstance(custom_msgs, str):
                custom_msgs = [m.strip() for m in custom_msgs.split("\n") if m.strip()]
            if custom_msgs:
                messages = [m for m in custom_msgs if m.strip()]
            else:
                messages = load_messages()

            custom_nc = settings.get("custom_nc", settings.get("custom_titles", []))
            if isinstance(custom_nc, str):
                custom_nc = [t.strip() for t in custom_nc.split("\n") if t.strip()]
            if custom_nc:
                renames = [t for t in custom_nc if t.strip()]
            else:
                renames = load_renames()

            if not messages:
                add_log("WARN", "No messages found (check custom input or message.txt)...", username, "multi")
                if not safe_sleep(5.0, uid):
                    break
                continue

            prefix_info = f" | Prefix: \"{msg_prefix}\"" if msg_prefix else ""
            source_info = "Account Custom Texts" if custom_msgs else "message.txt"
            nc_info = "Account Custom NC" if custom_nc else "nc.txt"
            add_log("CYCLE", f"--- 🚀 MULTI-GC CYCLE #{cycle_num} STARTING ({len(groups)} Groups | {source_info} & {nc_info}{prefix_info} | Cycle Delay {cycle_wait}s) ---", username, "multi")

            for g_idx, group in enumerate(groups, 1):
                if not bot_running.get(uid):
                    break

                gid = str(group.get("id"))
                gtitle = str(group.get("title") or f"Group {gid}")

                # 1. Send 1 Text to this GC
                if rotation_mode == "random":
                    raw_msg = random.choice(messages)
                else:
                    raw_msg = messages[(cycle_num + g_idx) % len(messages)]

                # SPAM Text uses Message Prefix ONLY!
                msg_text = format_spam_message(raw_msg, msg_prefix)
                success, result = send_message_to_group(
                    sess["session"],
                    sess["headers"],
                    gid,
                    msg_text,
                    sess["config"],
                    client=sess.get("client")
                )

                s_dict = stats.setdefault(uid, {"sent": 0, "failed": 0, "renamed": 0, "rename_failed": 0, "running": True, "log": ""})
                msg_preview = (msg_text[:40] + "...") if len(msg_text) > 40 else msg_text
                if success:
                    s_dict["sent"] = s_dict.get("sent", 0) + 1
                    s_dict["log"] = f"Delivered to '{gtitle}'"
                    add_log("SUCCESS", f"💬 [{g_idx}/{len(groups)}] [GC: '{gtitle}' | ID: {gid}] Text Delivered: \"{msg_preview}\" (Waiting {msg_delay}s)", username, "multi")
                    safe_sleep(msg_delay, uid)
                else:
                    s_dict["failed"] = s_dict.get("failed", 0) + 1
                    s_dict["log"] = f"Failed on '{gtitle}'"
                    add_log("ERROR", f"❌ [{g_idx}/{len(groups)}] [GC: '{gtitle}' | ID: {gid}] Text Failed: {result} (Waiting {msg_delay}s)", username, "multi")
                    if "login required" in str(result).lower() or "checkpoint" in str(result).lower():
                        report_login_required(uid, username, f"Message send error on '{gtitle}': {result}")
                        s_dict["log"] = "LOGIN REQUIRED - Session Expired"
                        safe_sleep(15.0, uid)
                    elif "rate limit" in str(result).lower() or "429" in str(result) or "wait" in str(result).lower():
                        add_log("WARN", f"Rate limit on [GC: '{gtitle}' | ID: {gid}]. Smart 15s pause...", username, "multi")
                        safe_sleep(15.0, uid)
                    else:
                        safe_sleep(min(5.0, msg_delay), uid)

                # 2. Rename this GC (1 NC) then next (User logic: 1 text -> 1 NC rename -> next GC)
                if rename_enabled and bot_running.get(uid):
                    cd_remain = int(rename_cooldowns.get(uid, 0) - time.time())
                    if cd_remain > 0:
                        # In cooldown! Skip renaming this GC and continue immediately!
                        pass
                    else:
                        if group_name_lock:
                            # NC Title uses Target Name ONLY!
                            new_title = format_nc_title(group_name_lock, target_name)
                        elif renames:
                            raw_nc = renames[nc_index % len(renames)]
                            nc_index += 1
                            new_title = format_nc_title(raw_nc, target_name)
                        else:
                            new_title = None

                        if new_title:
                            ren_ok, ren_res = rename_group_chat(
                                sess["session"],
                                sess["headers"],
                                gid,
                                new_title,
                                sess["config"],
                                client=sess.get("client")
                            )
                            s_dict = stats.setdefault(uid, {"sent": 0, "failed": 0, "renamed": 0, "rename_failed": 0, "running": True, "log": ""})
                            if ren_ok:
                                s_dict["renamed"] = s_dict.get("renamed", 0) + 1
                                group["title"] = new_title
                                add_log("SUCCESS", f"🏷️ [{g_idx}/{len(groups)}] [GC: '{gtitle}' | ID: {gid}] NC Renamed to: \"{new_title}\" (Waiting {ren_delay}s)", username, "multi")
                                safe_sleep(ren_delay, uid)
                            else:
                                s_dict["rename_failed"] = s_dict.get("rename_failed", 0) + 1
                                if "login required" in str(ren_res).lower() or "checkpoint" in str(ren_res).lower():
                                    report_login_required(uid, username, f"NC rename error on '{gtitle}': {ren_res}")
                                    s_dict["log"] = "LOGIN REQUIRED - Session Expired"
                                    safe_sleep(15.0, uid)
                                elif "rate limit" in str(ren_res).lower() or "429" in str(ren_res) or "wait" in str(ren_res).lower():
                                    rename_cooldowns[uid] = time.time() + 180  # 3m NC cooldown
                                    add_log("WARN", f"  ⚠️ [{g_idx}/{len(groups)}] [GC: '{gtitle}' | ID: {gid}] NC Rate limit. 3m cooldown set.", username, "multi")
                                    safe_sleep(2.0, uid)
                                else:
                                    add_log("WARN", f"  ⚠️ [{g_idx}/{len(groups)}] [GC: '{gtitle}' | ID: {gid}] NC Failed: {ren_res} (2s skip)", username, "multi")
                                    safe_sleep(2.0, uid)

            # 3. Cycle Cooldown Wait
            cycle_num += 1
            if bot_running.get(uid):
                add_log("CYCLE", f"✓ Multi-GC Cycle #{cycle_num - 1} complete across {len(groups)} groups! Cooling down {cycle_wait}s before Cycle #{cycle_num}...", username, "multi")
                safe_sleep(cycle_wait, uid)

        except Exception as e:
            add_log("WARN", f"Multi-GC loop auto-healed after glitch: {e}. Resuming non-stop...", username, "multi")
            safe_sleep(3.0, uid)

    bot_running[uid] = False
    active_worker_threads.pop(uid, None)
    if uid in live_stats:
        live_stats[uid]["running"] = False
    if uid in stats:
        stats[uid]["running"] = False
        stats[uid]["log"] = "Bot Stopped."
    add_log("WARN", f"⏹️ Multi-GC Engine stopped for @{username}", username, "multi")


# ================= GROUP PHOTO SPAMMER ENGINES & WORKER =================
def prepare_chat_photo(photo_path: str) -> str:
    """Instagram chat photo ke liye image ko standard RGB JPEG format (max 1920x1920) me convert karta hai."""
    temp_processed = os.path.join(TEMP_PHOTOS_DIR, f"chat_img_{uuid.uuid4().hex[:8]}.jpg")
    with Image.open(photo_path) as im:
        im = im.convert("RGB")
        im.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        im.save(temp_processed, "JPEG", quality=90)
    return temp_processed


def send_group_chat_photo(client, session, headers, thread_id, photo_path, config=None) -> tuple[bool, str]:
    """
    Sends photo directly into Instagram Direct group chat thread.
    Uses official instagrapi client direct_send_photo with authentic upload & broadcasting.
    """
    if not photo_path or not os.path.exists(photo_path):
        return False, "Photo file not found on disk"

    clean_temp = False
    proc_img = photo_path

    try:
        proc_img = prepare_chat_photo(photo_path)
        clean_temp = True
    except Exception as e:
        log("WARN", f"Pillow photo formatting note: {e}")
        proc_img = photo_path

    try:
        # 1. Primary: Instagrapi client direct_send_photo
        if client is not None:
            try:
                tid_int = int(thread_id)
                res = client.direct_send_photo(Path(proc_img), thread_ids=[tid_int])
                if res:
                    msg_id = getattr(res, 'id', None) or getattr(res, 'item_id', 'ok')
                    return True, f"Sent photo (Msg ID: {msg_id})"
            except Exception as cl_err:
                log("WARN", f"Instagrapi client direct_send_photo: {cl_err}")
                err_str = str(cl_err)
                if "login_required" in err_str.lower():
                    return False, f"Session expired/login required: {err_str}"

        # 2. Secondary fallback: If client uninitialized, re-auth with sessionid
        if session:
            try:
                sess_id = get_cookie(session, "sessionid", "")
                if sess_id:
                    temp_cl = Client()
                    temp_cl.delay_range = [0, 1]
                    temp_cl.login_by_sessionid(sess_id)
                    res = temp_cl.direct_send_photo(Path(proc_img), thread_ids=[int(thread_id)])
                    if res:
                        return True, "Sent photo (Re-authenticated client)"
            except Exception as e_retry:
                return False, f"Send failed: {e_retry}"

        return False, "Instagram rejected photo or client session unavailable"

    except Exception as e:
        return False, f"Photo dispatch error: {str(e)}"
    finally:
        if clean_temp and os.path.exists(proc_img):
            try:
                os.remove(proc_img)
            except Exception:
                pass


def group_photo_worker(uid):
    db = load_db()
    acc = db.get("accounts", {}).get(uid, {})
    username = acc.get("username", uid)

    cfg = acc.get("photo_changer_config", {})
    mode = cfg.get("mode", "single")
    thread_id = cfg.get("thread_id", "")
    between_photos = float(cfg.get("between_photos_sec", 20.0))
    cycle_wait = float(cfg.get("cycle_wait_sec", 60.0))
    photo_path = photo_changer_files.get(uid)

    round_num = 1
    add_log("INFO", f"📸 Group Photo Spammer engaged ({mode.upper()} Mode | Delay {between_photos}s | Cycle Delay {cycle_wait}s)", username, "photo_changer")

    try:
        while photo_running.get(uid, False):
            if not photo_running.get(uid, False):
                break
            if not photo_path or not os.path.exists(photo_path):
                if photo_running.get(uid, False):
                    add_log("WARN", "Photo file removed or unavailable. Stopping engine.", username, "photo_changer")
                break

            sess = get_account_session(uid)
            if not sess:
                add_log("ERROR", "Session lost. Pausing 10s...", username, "photo_changer")
                time.sleep(10)
                continue

            if mode == "single":
                tid = extract_thread_id(thread_id)
                if not tid:
                    add_log("ERROR", "No valid thread ID provided for Single GC.", username, "photo_changer")
                    break

                if not photo_running.get(uid, False):
                    break

                add_log("CYCLE", f"--- 📸 SINGLE GC PHOTO SPAM ROUND #{round_num} -> Target Thread {tid} ---", username, "photo_changer")
                success, msg = send_group_chat_photo(sess.get("client"), sess["session"], sess["headers"], tid, photo_path, sess.get("config"))

                if not photo_running.get(uid, False):
                    break

                if success:
                    photo_stats[uid]["sent"] = photo_stats[uid].get("sent", 0) + 1
                    photo_stats[uid]["changed"] = photo_stats[uid]["sent"]
                    sent_total = photo_stats[uid]["sent"]
                    add_log("SUCCESS", f"  ✓ Successfully sent photo into Thread {tid}! (Delivered #{sent_total}) | Waiting {between_photos}s...", username, "photo_changer")
                    
                    # Wait between photos
                    w_end = time.time() + max(0.1, between_photos)
                    while time.time() < w_end and photo_running.get(uid, False):
                        time.sleep(min(0.2, max(0.01, w_end - time.time())))
                else:
                    photo_stats[uid]["failed"] = photo_stats[uid].get("failed", 0) + 1
                    add_log("WARN", f"  ⚠️ Photo dispatch failed: {msg} | ⚡ 5s Quick Skip...", username, "photo_changer")
                    skip_end = time.time() + 5.0
                    while time.time() < skip_end and photo_running.get(uid, False):
                        time.sleep(min(0.2, max(0.01, skip_end - time.time())))

                if not photo_running.get(uid, False):
                    break

                # Cycle cooldown wait (if configured)
                if cycle_wait > 0:
                    round_num += 1
                    add_log("CYCLE", f"✓ Round #{round_num - 1} complete. Cycle cooldown {cycle_wait}s before Round #{round_num}...", username, "photo_changer")
                    c_end = time.time() + max(0.1, cycle_wait)
                    while time.time() < c_end and photo_running.get(uid, False):
                        time.sleep(min(0.2, max(0.01, c_end - time.time())))
                else:
                    round_num += 1

            else:  # Multi-GC Mode
                groups = [g for g in acc.get("groups", []) if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
                if not groups:
                    try:
                        hints = [acc.get("single_thread_id")] if acc.get("single_thread_id") else []
                        groups = fetch_group_chats(
                            sess["session"], sess["headers"], sess["config"],
                            client=sess.get("client"),
                            existing_groups=acc.get("groups", []),
                            thread_ids_hint=hints
                        )
                        groups = [g for g in groups if int(g.get("users_count", 0)) > 1 or len(g.get("users", [])) >= 1]
                        acc["groups"] = groups
                        db["accounts"][uid]["groups"] = groups
                        save_db(db)
                    except Exception:
                        pass
                if not groups:
                    add_log("WARN", "0 group chats found in inbox. Retrying in 15s...", username, "photo_changer")
                    time.sleep(15)
                    continue

                if not photo_running.get(uid, False):
                    break

                add_log("CYCLE", f"--- 📸 MULTI-GC PHOTO SPAM CYCLE #{round_num} STARTING ({len(groups)} Groups) ---", username, "photo_changer")
                for g_idx, group in enumerate(groups, 1):
                    if not photo_running.get(uid, False):
                        break
                    gid = str(group.get("id"))
                    gtitle = str(group.get("title") or f"Group {gid}")

                    success, msg = send_group_chat_photo(sess.get("client"), sess["session"], sess["headers"], gid, photo_path, sess.get("config"))

                    if not photo_running.get(uid, False):
                        break

                    if success:
                        photo_stats[uid]["sent"] = photo_stats[uid].get("sent", 0) + 1
                        photo_stats[uid]["changed"] = photo_stats[uid]["sent"]
                        add_log("SUCCESS", f"  ✓ [{g_idx}/{len(groups)}] Sent photo into '{gtitle}'! Waiting {between_photos}s...", username, "photo_changer")
                        
                        # Wait between photo delay
                        w_end = time.time() + max(0.1, between_photos)
                        while time.time() < w_end and photo_running.get(uid, False):
                            time.sleep(min(0.2, max(0.01, w_end - time.time())))
                    else:
                        photo_stats[uid]["failed"] = photo_stats[uid].get("failed", 0) + 1
                        add_log("WARN", f"  ⚠️ [{g_idx}/{len(groups)}] Photo dispatch failed: {msg} | ⚡ 5s Quick Skip...", username, "photo_changer")
                        skip_end = time.time() + 5.0
                        while time.time() < skip_end and photo_running.get(uid, False):
                            time.sleep(min(0.2, max(0.01, skip_end - time.time())))

                if not photo_running.get(uid, False):
                    break

                round_num += 1
                if cycle_wait > 0:
                    add_log("CYCLE", f"✓ Multi-GC Cycle #{round_num - 1} complete. Cooling down {cycle_wait}s...", username, "photo_changer")
                    c_end = time.time() + max(0.1, cycle_wait)
                    while time.time() < c_end and photo_running.get(uid, False):
                        time.sleep(min(0.2, max(0.01, c_end - time.time())))

    except Exception as e:
        add_log("ERROR", f"Photo spammer worker error: {e}", username, "photo_changer")
    finally:
        photo_running[uid] = False
        if uid in photo_stats:
            photo_stats[uid]["running"] = False
        p = photo_changer_files.pop(uid, None)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
        add_log("WARN", f"⏹️ Group Photo Spammer stopped for @{username}. 🔒 Temp image purged from disk.", username, "photo_changer")


# ================= GC CREATOR HELPERS & WORKER =================
def leave_thread_custom(cl, thread_id):
    try:
        response = cl.private_request(f"direct_v2/threads/{thread_id}/leave/", data={})
        if response.get("status") == "ok":
            return True
    except Exception:
        pass
    try:
        response = cl.private_request(f"direct_v2/threads/{thread_id}/leave/")
        if response.get("status") == "ok":
            return True
    except Exception:
        pass
    try:
        if hasattr(cl, 'direct_thread_leave'):
            return cl.direct_thread_leave(thread_id)
    except Exception:
        pass
    return False


def promote_admin_custom(cl, thread_id, user_id):
    endpoints_to_try = [
        f"direct_v2/threads/{thread_id}/add_admins/",
        f"direct_v2/threads/{thread_id}/make_admin/",
        f"direct_v2/threads/{thread_id}/admin/"
    ]
    data = {"user_ids": json.dumps([str(user_id)])}
    for endpoint in endpoints_to_try:
        try:
            response = cl.private_request(endpoint, data=data)
            if response.get("status") == "ok":
                return True
        except Exception:
            continue
    try:
        if hasattr(cl, 'direct_thread_admin_promote'):
            return cl.direct_thread_admin_promote(thread_id, [str(user_id)])
    except Exception:
        pass
    return False


def gc_worker(uid):
    db = load_db()
    acc = db.get("gc_accounts", {}).get(uid, {})
    cl = gc_clients.get(uid)

    if not cl:
        acc_id = acc.get("account_id")
        if acc_id:
            sess = get_account_session(acc_id)
            if sess and sess.get("client"):
                cl = sess["client"]
                gc_clients[uid] = cl

    if not cl and acc.get("sessionid"):
        try:
            sess_id = acc["sessionid"]
            cl = Client()
            cl.delay_range = [0, 1]
            cl.request_timeout = 15
            ds_user_id = sess_id.split("%3A")[0].split(":")[0] if ("%3A" in sess_id or ":" in sess_id) else ""
            cl.authorization_data = {
                "ds_user_id": str(ds_user_id),
                "sessionid": sess_id,
                "should_use_header_over_cookies": True
            }
            cl.private.cookies.set("sessionid", sess_id, domain=".instagram.com")
            cl.private.cookies.set("ds_user_id", str(ds_user_id), domain=".instagram.com")
            cl.user_id = str(ds_user_id)
            gc_clients[uid] = cl
        except Exception as e:
            add_log("ERROR", f"Failed to initialize GC client: {e}", uid, "gc_creator")
            return

    if not cl:
        return

    retry = 0
    created = 0
    greetings = [
        "Hello everyone! Welcome to the group.",
        "Hey guys, group chat discussion activated.",
        "Official group established.",
        "Welcome to the thread everyone.",
        "Clan discussion room online."
    ]
    username_to_id_cache = {}

    def update_log(msg):
        if uid in gc_stats:
            gc_stats[uid]["log"] = msg
        add_log("INFO", f"[{uid}] {msg}", uid, "gc_creator")

    def safe_wait(seconds):
        end = time.time() + seconds
        while time.time() < end:
            if not gc_running.get(uid, False):
                return False
            time.sleep(0.3)
        return True

    # Safe Profile / Session check that does NOT trigger Instagram anti-bot lockouts
    session_valid = False
    try:
        if getattr(cl, 'user_id', None) and str(cl.user_id).isdigit():
            session_valid = True
            update_log(f"Profile Validated: ID {cl.user_id}")
        else:
            info = cl.account_info()
            if info and getattr(info, 'pk', None):
                session_valid = True
                update_log(f"Profile Validated: @{info.username} (PK #{info.pk})")
    except Exception:
        # Fallback check cookies
        try:
            if cl.private.cookies.get("sessionid"):
                session_valid = True
                update_log("Session Authenticated (Vault Cookie Verified)")
        except Exception:
            pass

    if not session_valid:
        update_log("Session Verification Warning. Proceeding cautiously...")

    while gc_running.get(uid, False):
        try:
            db = load_db()
            cfg = db.get("gc_accounts", {}).get(uid)
            if not cfg:
                time.sleep(5)
                continue

            delay = int(cfg.get("delay", 5))
            total = int(cfg.get("groups", 0))

            raw_targets = cfg.get("targets", [])
            targets = [t.strip() for t in raw_targets if t.strip()]
            admin_target = cfg.get("admin_target", "").strip()

            final_members = list(set(targets))
            if admin_target and admin_target not in final_members:
                final_members.append(admin_target)

            if len(final_members) < 2:
                update_log("Need at least 2 valid targets to create group.")
                if not safe_wait(5):
                    break
                continue

            user_ids = []
            admin_id = None

            for uname in final_members:
                if not gc_running.get(uid):
                    break
                if uname in username_to_id_cache:
                    resolved_id = username_to_id_cache[uname]
                    user_ids.append(resolved_id)
                    if uname.lower() == admin_target.lower():
                        admin_id = resolved_id
                elif uname.isdigit():
                    user_ids.append(int(uname))
                    username_to_id_cache[uname] = int(uname)
                    if uname == admin_target:
                        admin_id = int(uname)
                else:
                    try:
                        resolved_id = cl.user_id_from_username(uname)
                        user_ids.append(int(resolved_id))
                        username_to_id_cache[uname] = int(resolved_id)
                        if uname.lower() == admin_target.lower():
                            admin_id = int(resolved_id)
                        if not safe_wait(1.5):
                            break
                    except Exception:
                        continue

            if not gc_running.get(uid):
                break

            if len(user_ids) < 2:
                update_log("Failed to resolve 2 targets. Retrying...")
                if not safe_wait(8):
                    break
                continue

            # Create Group
            try:
                update_log(f"Creating GC with {len(user_ids)} members...")
                chosen_text = random.choice(greetings)
                thread_id = None

                # Primary: instagrapi direct_thread_create
                try:
                    thread_id = str(cl.direct_thread_create(user_ids=user_ids) or "")
                except Exception as e_create:
                    add_log("INFO", f"direct_thread_create: {e_create}. Fallback to direct_send...", uid, "gc_creator")

                # Secondary: instagrapi direct_send broadcast
                if not thread_id:
                    try:
                        msg = cl.direct_send(text=chosen_text, user_ids=user_ids)
                        thread_id = str(getattr(msg, 'thread_id', None) or "")
                    except Exception as e_send:
                        add_log("WARN", f"direct_send broadcast error: {e_send}", uid, "gc_creator")

                if not thread_id:
                    raise Exception("Instagram did not return Thread ID. Members may restrict DMs or rate limited.")

                try:
                    cl.direct_send(text=chosen_text, thread_ids=[int(thread_id)])
                except Exception:
                    pass

                update_log(f"Created GC #{created+1}: Thread ID {thread_id}")
                add_log("SUCCESS", f"✓ Created Group Chat #{created+1}: Thread ID {thread_id}", uid, "gc_creator")
            except Exception as e:
                update_log(f"Create error: {e}")
                retry += 1
                if uid in gc_stats:
                    gc_stats[uid]["failed"] = gc_stats[uid].get("failed", 0) + 1
                if retry >= 5:
                    update_log("Anti-spam pause: 60s cooldown...")
                    retry = 0
                    if not safe_wait(60):
                        break
                    continue
                if not safe_wait(5):
                    break
                continue

            if not safe_wait(random.uniform(2, 4)):
                break

            # Promote Admin
            if admin_id:
                update_log(f"Promoting @{admin_target} to Group Admin...")
                if not safe_wait(2):
                    break
                try:
                    promoted = promote_admin_custom(cl, thread_id, admin_id)
                    if promoted:
                        update_log(f"✓ Promoted @{admin_target} to Admin!")
                        add_log("SUCCESS", f"✓ Promoted @{admin_target} to Admin in Thread {thread_id}", uid, "gc_creator")
                    else:
                        update_log(f"Admin promote sent for @{admin_target}")
                except Exception as ep:
                    add_log("WARN", f"Admin promote notice: {ep}", uid, "gc_creator")
                if not safe_wait(2):
                    break

            # Auto-leave after 15s to bypass IG mass creation limits
            update_log("Anti-Ban Guard: Waiting 15s before auto-leaving GC...")
            if not safe_wait(15):
                break

            try:
                left = leave_thread_custom(cl, thread_id)
                if left:
                    update_log(f"✓ Left GC #{created+1} successfully (Anti-Ban Guard Active).")
                    add_log("INFO", f"✓ Bot left Thread {thread_id} after 15s guard", uid, "gc_creator")
                else:
                    update_log("Leave GC processed.")
            except Exception as el:
                add_log("WARN", f"Leave thread notice: {el}", uid, "gc_creator")

            created += 1
            retry = 0
            if uid in gc_stats:
                gc_stats[uid]["groups"] = gc_stats[uid].get("groups", 0) + 1

            if total > 0 and created >= total:
                update_log(f"Target of {total} groups reached successfully.")
                break

            if not safe_wait(delay):
                break

        except Exception as e:
            update_log(f"GC Creator loop exception: {e}")
            retry += 1
            if uid in gc_stats:
                gc_stats[uid]["failed"] = gc_stats[uid].get("failed", 0) + 1
            if retry >= 5:
                update_log("Anti-spam pause: 60s cooldown...")
                retry = 0
                if not safe_wait(60):
                    break
            else:
                if not safe_wait(10):
                    break

    gc_running[uid] = False
    if uid in gc_live:
        gc_live[uid]["running"] = False
    if uid in gc_stats:
        gc_stats[uid]["running"] = False
    update_log("GC Creator Bot Stopped.")
    add_log("WARN", f"⏹️ GC Creator bot #{uid} stopped", uid, "gc_creator")


# ================= GC ROUTES =================
@app.route("/gc_add_account", methods=["POST"])
def gc_add_account():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    data = request.json or {}
    sessionid = data.get("sessionid", "").strip()
    account_id = data.get("account_id", "").strip()
    db = load_db()

    account_username = ""
    cl = None

    # If account_id provided from panel, reuse verified session
    if account_id and account_id in db.get("accounts", {}):
        acc = db["accounts"][account_id]
        if not sessionid:
            sessionid = acc.get("sessionid", "")
        account_username = acc.get("username", "")

        sess = get_account_session(account_id)
        if sess and sess.get("client"):
            cl = sess["client"]
            if not account_username:
                account_username = sess.get("username", "")

    if not sessionid:
        return jsonify({"status": "error", "message": "Please select a connected account or enter a Session ID"})

    # Setup client safely without triggering mobile checkpoint logout
    if not cl:
        try:
            cl = Client()
            cl.delay_range = [0, 1]
            cl.request_timeout = 15
            ds_user_id = sessionid.split("%3A")[0].split(":")[0] if ("%3A" in sessionid or ":" in sessionid) else ""
            cl.authorization_data = {
                "ds_user_id": str(ds_user_id),
                "sessionid": sessionid,
                "should_use_header_over_cookies": True
            }
            cl.private.cookies.set("sessionid", sessionid, domain=".instagram.com")
            cl.private.cookies.set("ds_user_id", str(ds_user_id), domain=".instagram.com")
            cl.user_id = str(ds_user_id)
        except Exception as e:
            return jsonify({"status": "login_failed", "error": f"Invalid Session: {e}"})

    uid = "gc_" + next_gc_uid(db)
    gc_clients[uid] = cl
    gc_running[uid] = False

    raw_targets = data.get("target", "")
    targets = [x.strip().lstrip("@") for x in str(raw_targets).replace(",", "\n").split("\n") if x.strip()]
    admin_target = data.get("admin_target", "").strip().lstrip("@")
    current_user = session.get("user", "OWNER")

    db["gc_accounts"][uid] = {
        "sessionid": sessionid,
        "username": account_username or f"bot_{uid}",
        "account_id": account_id,
        "delay": int(data.get("delay", 5)),
        "targets": targets,
        "admin_target": admin_target,
        "groups": int(data.get("groups", 0)),
        "cycle_wait": int(data.get("cycle_wait", 20)),
        "owner": current_user
    }

    gc_stats[uid] = {
        "user": current_user,
        "account": uid,
        "groups": 0,
        "failed": 0,
        "running": False,
        "uptime": 0,
        "log": "Account Ready"
    }
    gc_live[uid] = {"running": False, "started": None}
    save_db(db)
    add_log("SUCCESS", f"Added GC Creator bot #{uid} (@{account_username or uid})", uid, "gc_creator")
    return jsonify({"status": "ok", "uid": uid})


@app.route("/gc_start", methods=["POST"])
def gc_start():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    data = request.get_json(silent=True) or {}
    uid = data.get("uid")
    db = load_db()

    if not uid or uid not in db.get("gc_accounts", {}):
        return jsonify({"status": "invalid"})

    if session.get("role") != "owner" and db["gc_accounts"][uid].get("owner") != session.get("user"):
        return jsonify({"status": "denied"})

    if gc_running.get(uid):
        return jsonify({"status": "already_running"})

    gc_running[uid] = True
    gc_live[uid] = {"running": True, "started": time.time()}
    if uid not in gc_stats:
        gc_stats[uid] = {"user": db["gc_accounts"][uid].get("owner", ""), "account": uid, "groups": 0, "failed": 0, "running": True, "uptime": 0, "log": "Starting..."}
    else:
        gc_stats[uid]["running"] = True

    threading.Thread(target=gc_worker, args=(uid,), daemon=True).start()
    add_log("SUCCESS", f"Started GC Creator Bot #{uid}", uid, "gc_creator")
    return jsonify({"status": "started"})


@app.route("/gc_stop", methods=["POST"])
def gc_stop():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    data = request.get_json(silent=True) or {}
    uid = data.get("uid")
    db = load_db()

    if uid in db.get("gc_accounts", {}):
        if session.get("role") != "owner" and db["gc_accounts"][uid].get("owner") != session.get("user"):
            return jsonify({"status": "denied"})

    gc_running[uid] = False
    if uid in gc_live:
        gc_live[uid]["running"] = False
    if uid in gc_stats:
        gc_stats[uid]["running"] = False
        gc_stats[uid]["log"] = "Bot Stopped."

    add_log("WARN", f"Stopped GC Creator Bot #{uid}", uid, "gc_creator")
    return jsonify({"status": "stopped"})


@app.route("/gc_delete_account", methods=["POST"])
def gc_delete_account():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    db = load_db()
    data = request.get_json(silent=True) or {}
    uid = data.get("uid")

    if not uid or uid not in db.get("gc_accounts", {}):
        return jsonify({"status": "invalid"})

    if session.get("role") != "owner" and db["gc_accounts"][uid].get("owner") != session.get("user"):
        return jsonify({"status": "denied"})

    gc_running[uid] = False
    gc_running.pop(uid, None)
    gc_clients.pop(uid, None)
    gc_stats.pop(uid, None)
    gc_live.pop(uid, None)

    del db["gc_accounts"][uid]
    save_db(db)
    add_log("WARN", f"Deleted GC Creator Bot #{uid}", uid, "gc_creator")
    return jsonify({"status": "ok"})


@app.route("/gc_edit_account", methods=["POST"])
def gc_edit_account():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    db = load_db()
    data = request.get_json(silent=True) or {}
    uid = data.get("uid")

    if not uid or uid not in db.get("gc_accounts", {}):
        return jsonify({"status": "invalid"})

    if session.get("role") != "owner" and db["gc_accounts"][uid].get("owner") != session.get("user"):
        return jsonify({"status": "denied"})

    raw_targets = data.get("targets", "")
    targets = [line.strip() for line in str(raw_targets).replace(",", "\n").split("\n") if line.strip()]

    db["gc_accounts"][uid]["delay"] = int(data.get("delay", 5))
    db["gc_accounts"][uid]["targets"] = targets
    db["gc_accounts"][uid]["admin_target"] = data.get("admin_target", "").strip()
    db["gc_accounts"][uid]["groups"] = int(data.get("groups", 0))
    db["gc_accounts"][uid]["cycle_wait"] = int(data.get("cycle_wait", 20))

    save_db(db)
    add_log("INFO", f"Updated settings for GC Bot #{uid}", uid, "gc_creator")
    return jsonify({"status": "ok"})


@app.route("/gc_start_all", methods=["POST"])
def gc_start_all():
    if "role" not in session:
        return jsonify({"status": "login_required"})
    db = load_db()
    is_owner = session.get("role") == "owner"
    current_user = session.get("user")

    started_count = 0
    for uid, acc in db.get("gc_accounts", {}).items():
        if is_owner or acc.get("owner") == current_user:
            if not gc_running.get(uid):
                try:
                    gc_running[uid] = True
                    gc_live[uid] = {"running": True, "started": time.time()}
                    gc_stats[uid] = {"user": acc.get("owner", ""), "account": uid, "groups": 0, "failed": 0, "running": True, "uptime": 0, "log": "Starting..."}
                    threading.Thread(target=gc_worker, args=(uid,), daemon=True).start()
                    started_count += 1
                except Exception:
                    pass
    return jsonify({"status": "ok", "started": started_count})


@app.route("/gc_stop_all", methods=["POST"])
def gc_stop_all():
    if "role" not in session:
        return jsonify({"status": "login_required"})
    db = load_db()
    is_owner = session.get("role") == "owner"
    current_user = session.get("user")

    stopped_count = 0
    for uid, acc in db.get("gc_accounts", {}).items():
        if is_owner or acc.get("owner") == current_user:
            gc_running[uid] = False
            if uid in gc_live:
                gc_live[uid]["running"] = False
            if uid in gc_stats:
                gc_stats[uid]["running"] = False
                gc_stats[uid]["log"] = "Bot Stopped."
            stopped_count += 1
    return jsonify({"status": "ok", "stopped": stopped_count})


@app.route("/gc_status")
def gc_status():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    db = load_db()
    is_owner = session.get("role") == "owner"
    current_user = session.get("user")

    gc_accounts = db.get("gc_accounts", {})
    accounts = {}
    visible = {}

    if is_owner:
        accounts = gc_accounts
        for uid, acc in gc_accounts.items():
            if uid not in gc_stats:
                gc_stats[uid] = {
                    "user": acc.get("owner", ""),
                    "account": uid,
                    "groups": 0,
                    "failed": 0,
                    "running": False,
                    "uptime": 0,
                    "log": "Awaiting Start"
                }
            visible[uid] = gc_stats[uid]
    else:
        for uid, acc in gc_accounts.items():
            if str(acc.get("owner") or "OWNER").strip().upper() == str(current_user).strip().upper():
                accounts[uid] = acc
                if uid not in gc_stats:
                    gc_stats[uid] = {
                        "user": current_user,
                        "account": uid,
                        "groups": 0,
                        "failed": 0,
                        "running": False,
                        "uptime": 0,
                        "log": "Awaiting Start"
                    }
                visible[uid] = gc_stats[uid]

    for uid, s in visible.items():
        if gc_live.get(uid, {}).get("running"):
            s["running"] = True
            started_t = gc_live[uid].get("started")
            s["uptime"] = int(time.time() - started_t) if started_t else 0
        else:
            s["running"] = False
            s["uptime"] = 0

    return jsonify({
        "accounts": accounts,
        "stats": visible,
        "role": session.get("role"),
        "user": current_user
    })


# ================= OWNER USER & SYSTEM MANAGEMENT =================
@app.route("/add_user", methods=["POST"])
def add_user():
    if session.get("role") != "owner":
        return jsonify({"status": "denied"})

    data = request.json or {}
    user = data.get("user", "").strip()
    password = data.get("pass", "").strip()
    name = data.get("name", "").strip() or user

    if not user or not password:
        return jsonify({"status": "error", "message": "Username and password required"})

    db = load_db()
    if user in db.get("users", {}):
        return jsonify({"status": "already_exists", "message": "User already exists"})

    db["users"][user] = {
        "password": password,
        "name": name
    }
    save_db(db)
    add_log("SUCCESS", f"New user created: @{user}", "OWNER", "system")
    return jsonify({"status": "ok"})


@app.route("/delete_user", methods=["POST"])
def delete_user():
    if session.get("role") != "owner":
        return jsonify({"status": "denied"})

    data = request.get_json(silent=True) or {}
    user = data.get("user")
    if not user:
        return jsonify({"status": "error", "message": "Missing username"})

    db = load_db()
    if user in db.get("users", {}):
        del db["users"][user]

        for uid in list(db.get("accounts", {}).keys()):
            if db["accounts"][uid].get("owner") == user:
                bot_running[uid] = False
                account_sessions.pop(uid, None)
                stats.pop(uid, None)
                live_stats.pop(uid, None)
                del db["accounts"][uid]

        for uid in list(db.get("gc_accounts", {}).keys()):
            if db["gc_accounts"][uid].get("owner") == user:
                gc_running[uid] = False
                gc_clients.pop(uid, None)
                gc_stats.pop(uid, None)
                gc_live.pop(uid, None)
                del db["gc_accounts"][uid]

        save_db(db)
        add_log("WARN", f"User deleted: @{user}", "OWNER", "system")
        return jsonify({"status": "ok"})
    return jsonify({"status": "not_found"})


@app.route("/update_user_name", methods=["POST"])
def update_user_name():
    if session.get("role") != "owner":
        return jsonify({"status": "denied"})
    db = load_db()
    data = request.get_json(silent=True) or {}
    user = data.get("user")
    name = str(data.get("name", "")).strip()

    if user and user in db.get("users", {}):
        db["users"][user]["name"] = name or user
        save_db(db)
        add_log("INFO", f"User @{user} display name updated to '{name}'", "OWNER", "system")
        return jsonify({"status": "ok"})
    return jsonify({"status": "not_found"})


@app.route("/update_user_password", methods=["POST"])
def update_user_password():
    if session.get("role") != "owner":
        return jsonify({"status": "denied"})
    db = load_db()
    data = request.get_json(silent=True) or {}
    user = data.get("user")
    new_pass = str(data.get("password", "")).strip()

    if not new_pass:
        return jsonify({"status": "error", "message": "Password cannot be empty"})

    if user and user in db.get("users", {}):
        db["users"][user]["password"] = new_pass
        save_db(db)
        add_log("INFO", f"Password changed for user @{user}", "OWNER", "system")
        return jsonify({"status": "ok"})
    return jsonify({"status": "not_found"})


@app.route("/update_members", methods=["POST"])
def update_members():
    if session.get("role") != "owner":
        return jsonify({"status": "denied"})
    db = load_db()
    data = request.get_json(silent=True) or {}
    db["members"] = data.get("members", [])
    save_db(db)
    return jsonify({"status": "ok"})



@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "message": "Bot process alive",
        "panel": "SERVER GODCLAN V2"
    })

@app.route("/status")
def status():
    if "role" not in session:
        return jsonify({"status": "login_required"})

    db = load_db()
    is_owner = session.get("role") == "owner"
    current_user = session.get("user")

    accounts = {}
    visible_stats = {}
    users_data = {}

    if is_owner:
        accounts = db.get("accounts", {})
        for uname, uinfo in db.get("users", {}).items():
            user_prem = [k for k, v in db.get("accounts", {}).items() if v.get("owner") == uname]
            user_gc = [k for k, v in db.get("gc_accounts", {}).items() if v.get("owner") == uname]
            running_prem = sum(1 for k in user_prem if bot_running.get(k))
            running_gc = sum(1 for k in user_gc if gc_running.get(k))

            users_data[uname] = {
                "name": uinfo.get("name", uname),
                "password": uinfo.get("password", ""),
                "premium_count": len(user_prem),
                "gc_count": len(user_gc),
                "running_count": running_prem + running_gc
            }

        for uid, acc in accounts.items():
            if uid not in stats:
                stats[uid] = {
                    "user": acc.get("owner", ""),
                    "account": uid,
                    "sent": 0,
                    "failed": 0,
                    "renamed": 0,
                    "rename_failed": 0,
                    "running": False,
                    "started": None,
                    "log": "Ready"
                }
            visible_stats[uid] = stats[uid]
    else:
        for uid, acc in db.get("accounts", {}).items():
            if acc.get("owner") == current_user:
                accounts[uid] = acc
                if uid not in stats:
                    stats[uid] = {
                        "user": current_user,
                        "account": uid,
                        "sent": 0,
                        "failed": 0,
                        "renamed": 0,
                        "rename_failed": 0,
                        "running": False,
                        "started": None,
                        "log": "Ready"
                    }
                visible_stats[uid] = stats[uid]

    for uid, s in visible_stats.items():
        if uid in live_stats and live_stats[uid].get("running"):
            s["running"] = True
            started_t = live_stats[uid].get("started")
            s["uptime"] = int(time.time() - started_t) if started_t else 0
        else:
            s["running"] = False
            s["uptime"] = 0

    all_prem_accs = db.get("accounts", {})
    all_gc_accs = db.get("gc_accounts", {})
    global_summary = {
        "total_users": len(db.get("users", {})),
        "total_premium": len(all_prem_accs),
        "total_gc": len(all_gc_accs),
        "running_premium": sum(1 for r in bot_running.values() if r),
        "running_gc": sum(1 for r in gc_running.values() if r),
        "total_sent": sum(s.get("sent", 0) for s in stats.values()),
        "total_renamed": sum(s.get("renamed", 0) for s in stats.values()),
        "total_groups": sum(s.get("groups", 0) for s in gc_stats.values())
    }

    return jsonify({
        "role": session.get("role"),
        "user": current_user,
        "name": session.get("name"),
        "users": users_data,
        "members": db.get("members", []),
        "accounts": accounts,
        "stats": visible_stats,
        "global_summary": global_summary
    })



# ================= CLOUDFLARE PUBLIC TUNNEL INTEGRATION =================
import atexit

PUBLIC_TUNNEL_URL = ""
cf_subprocess = None

def cleanup_cloudflared():
    global cf_subprocess
    if cf_subprocess and cf_subprocess.poll() is None:
        try:
            cf_subprocess.terminate()
        except Exception:
            pass

atexit.register(cleanup_cloudflared)

def start_cloudflared_tunnel(port=20823):
    global PUBLIC_TUNNEL_URL, cf_subprocess
    time.sleep(2.0)  # Wait for Flask to start listening
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cf_candidates = [
        os.path.join(script_dir, "cloudflared.exe"),
        "cloudflared.exe",
        shutil.which("cloudflared"),
        r"C:\Users\KING\Videos\PANNEL\cloudflared.exe",
        r"C:\Users\KING\Downloads\instagram_bot\instagram_bot\cloudflared.exe"
    ]
    cf_bin = None
    for cand in cf_candidates:
        if cand and os.path.exists(cand):
            cf_bin = cand
            break

    if not cf_bin:
        print("[!] cloudflared.exe not found on disk. Skipping auto-tunnel.")
        return

    url_regex = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")

    while True:
        try:
            cmd = [
                cf_bin,
                "tunnel",
                "--url", f"http://127.0.0.1:{port}",
                "--no-autoupdate"
            ]
            print(f"[*] 🚀 Launching Cloudflare Public Tunnel for SERVER GODCLAN V2 on Port {port}...")

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            cf_subprocess = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags
            )

            url_found = False
            for line in cf_subprocess.stdout:
                line_str = line.strip()
                if not url_found:
                    m = url_regex.search(line_str)
                    if m:
                        found_url = m.group(0)
                        PUBLIC_TUNNEL_URL = found_url
                        url_found = True
                        try:
                            with open(os.path.join(script_dir, "tunnel_url.txt"), "w", encoding="utf-8") as tf:
                                tf.write(found_url)
                        except Exception:
                            pass

                        print("\n" + "=" * 65)
                        print("⚡⚡⚡ CLOUDFLARE PUBLIC TUNNEL LIVE URL ⚡⚡⚡")
                        print(f"🌐 DIRECT LINK: {found_url}")
                        print(f"🔒 SAVED TO:    tunnel_url.txt")
                        print("=" * 65 + "\n")
                        add_log("SUCCESS", f"🌐 Cloudflare Public Tunnel active: {found_url}", "SYSTEM", "system")

            ret = cf_subprocess.wait() if cf_subprocess else -1
            print(f"[!] Cloudflare tunnel process exited (code {ret}). Reconnecting in 3s...")
            PUBLIC_TUNNEL_URL = ""
            add_log("WARN", f"⚠️ Cloudflare tunnel connection dropped (code {ret}). Auto-reconnecting...", "SYSTEM", "system")
        except Exception as e:
            print(f"[!] Cloudflare tunnel error: {e}. Retrying in 5s...")
            PUBLIC_TUNNEL_URL = ""
            time.sleep(2.0)

        time.sleep(3.0)


@app.route("/api/tunnel_url")
def api_tunnel_url():
    url = PUBLIC_TUNNEL_URL
    if not url and os.path.exists("tunnel_url.txt"):
        try:
            with open("tunnel_url.txt", "r", encoding="utf-8") as f:
                url = f.read().strip()
        except Exception:
            pass
    return jsonify({"url": url})


@app.route("/logo.png")
def serve_logo_root():
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), "logo.png")


@app.route("/favicon.ico")
def serve_favicon_root():
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), "favicon.ico")


@app.route("/favicon.png")
def serve_favicon_png_root():
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), "favicon.png")


@app.route("/apple-touch-icon.png")
def serve_apple_touch_icon():
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), "apple-touch-icon.png")


@app.route("/manifest.json")
def serve_manifest():
    from flask import send_from_directory
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"), "manifest.json", mimetype="application/manifest+json")


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ================= 24/7 AUTO-HEALING WATCHDOG SUPERVISOR =================
def bot_supervisor():
    """
    24/7 Auto-Healing Supervisor:
    Monitors all active bots. If any bot thread terminates or crashes while bot_running is True,
    it automatically revives and restarts the worker thread within seconds!
    Spam NEVER STOPS until user explicitly clicks STOP from the panel!
    """
    time.sleep(3)  # Initial startup grace period
    while True:
        try:
            time.sleep(10)
            db = load_db()
            accounts = db.get("accounts", {})
            for uid, acc in accounts.items():
                uid_str = str(uid)
                if bot_running.get(uid_str, False):
                    th = active_worker_threads.get(uid_str)
                    if th is None or not th.is_alive():
                        uname = acc.get("username", uid_str)
                        mode = acc.get("mode", "single_gc")
                        add_log("WARN", f"🛡️ Non-Stop Watchdog: Auto-reviving {mode.upper()} engine for @{uname}...", uname, "system")
                        start_bot_worker(uid_str, mode)
        except Exception:
            time.sleep(5)

# Start background watchdog supervisor
threading.Thread(target=bot_supervisor, daemon=True).start()


# ================= RUN SERVER =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"============================================================")
    print(f"       ⚡ SERVER GODCLAN V2 • AUTONOMOUS MATRIX ⚡          ")
    print(f"               OWNER KEY: SCAR@12345                        ")
    print(f"               RUNNING ON PORT: {port}                      ")
    print(f"============================================================")
    threading.Thread(target=start_cloudflared_tunnel, args=(port,), daemon=True).start()
    app.run(host="0.0.0.0", port=port, debug=False)
