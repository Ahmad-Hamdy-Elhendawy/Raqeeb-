import json
import os
import urllib.request
import urllib.error
import boto3
from boto3.dynamodb.conditions import Key
from decimal import Decimal
import time

dynamodb = boto3.resource("dynamodb")

# Table for ping results (data to analyze)
ping_table = dynamodb.Table(os.environ["PING_RESULTS_TABLE"])

# Table for website metadata (contains Telegram IDs)
websites_table = dynamodb.Table(os.environ["SITE_MONITOR_WEBSITES"])

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Gemini API endpoint - Using gemini-2.5-flash (free tier compatible)
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def safe(val, fallback="—"):
    if val is None or val == "":
        return fallback
    return val


def fetch_website_info(website_id):
    """Fetch website info from the websites table including Telegram IDs"""
    if not website_id:
        print("No website_id provided")
        return None
    
    print(f"Fetching website info for ID: {website_id}")
    
    try:
        resp = websites_table.get_item(Key={"id": website_id})
        item = resp.get("Item")
        
        if item:
            print(f"Found website record with chat IDs - Owner: {item.get('user_telegram_id')}, Dev: {item.get('dev_telegram_id')}")
            return item
        else:
            print(f"No website found for ID: {website_id}")
            return None
    except Exception as e:
        print(f"Error fetching from websites table: {e}")
        return None


def fetch_latest_pings(website_id, limit=10):
    """Fetch latest N ping results from ping results table"""
    if not website_id:
        print("No website_id provided")
        return []
    
    print(f"Fetching latest {limit} pings for website_id: {website_id}")
    
    try:
        resp = ping_table.query(
            KeyConditionExpression=Key("website_id").eq(website_id),
            ScanIndexForward=False,
            Limit=limit
        )
        items = resp.get("Items", [])
        print(f"Found {len(items)} ping records")
        return items
    except Exception as e:
        print(f"Error fetching pings from DynamoDB: {e}")
        return []


def analyze_multiple_pings(pings):
    """Analyze multiple ping results and detect patterns"""
    if not pings:
        return None
    
    total = len(pings)
    successes = 0
    failures = 0
    consecutive_failures = 0
    max_consecutive_failures = 0
    latencies = []
    
    for ping in pings:
        is_success = ping.get('success', False)
        
        if is_success:
            successes += 1
            consecutive_failures = 0
            latency = float(ping.get('avg_latency_ms', 0))
            if latency > 0:
                latencies.append(latency)
        else:
            failures += 1
            consecutive_failures += 1
            max_consecutive_failures = max(max_consecutive_failures, consecutive_failures)
    
    success_rate = (successes / total * 100) if total > 0 else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    
    latest_success = pings[0].get('success', False) if pings else False
    latest_status = "UP" if latest_success else "DOWN"
    
    if total >= 6:
        half = total // 2
        recent_success = sum(1 for p in pings[:half] if p.get('success', False))
        older_success = sum(1 for p in pings[half:] if p.get('success', False))
        
        if recent_success > older_success:
            trend = "improving"
        elif recent_success < older_success:
            trend = "degrading"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"
    
    return {
        'total_pings': total,
        'successful': successes,
        'failed': failures,
        'success_rate': success_rate,
        'avg_latency': avg_latency,
        'max_consecutive_failures': max_consecutive_failures,
        'latest_status': latest_status,
        'trend': trend,
        'latest_ping': pings[0] if pings else None,
        'all_pings': pings
    }


def send_single_telegram(chat_id, message):
    """Send a single Telegram message"""
    if not chat_id:
        print("No chat_id provided, skipping Telegram send")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"✅ Telegram message sent successfully to {chat_id}")
                return True
            else:
                print(f"❌ Telegram error: {result}")
                return False
    except Exception as e:
        print(f"❌ Telegram send failed to {chat_id}: {e}")
        return False


def send_telegram(chat_id, message):
    """Send Telegram message with splitting if too long"""
    if not chat_id:
        print("No chat_id provided, skipping Telegram send")
        return False
    
    # Telegram has a 4096 character limit
    if len(message) > 4000:
        print(f"📏 Message too long ({len(message)} chars), splitting...")
        chunks = []
        current_chunk = ""
        for line in message.split('\n'):
            if len(current_chunk) + len(line) + 1 > 4000:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += line + '\n'
        if current_chunk:
            chunks.append(current_chunk)
        
        for chunk in chunks:
            if not send_single_telegram(chat_id, chunk):
                return False
        return True
    else:
        return send_single_telegram(chat_id, message)


def call_gemini_api(prompt, retry_count=0):
    """Call Gemini API via REST API with retry logic for free tier limits"""
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY not set")
        return None
    
    url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"
    
    # Increased maxOutputTokens to allow full responses
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2048,  # Increased from 1024 to 2048
            "topP": 0.95
        }
    }
    
    try:
        print("🤖 Calling Gemini API for UP message...")
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            response = json.loads(resp.read())
            
            if "candidates" in response and len(response["candidates"]) > 0:
                candidate = response["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    text = candidate["content"]["parts"][0].get("text", "")
                    if text:
                        # Check if response was cut off
                        finish_reason = candidate.get("finishReason", "UNKNOWN")
                        if finish_reason == "MAX_TOKENS":
                            print(f"⚠️ Response was cut off due to MAX_TOKENS limit")
                        print(f"✅ Gemini API call successful ({len(text)} chars, finish reason: {finish_reason})")
                        return text.strip()
            
            print(f"⚠️ Unexpected response format")
            return None
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        
        if e.code == 429 and retry_count < 3:
            wait_time = 2 ** retry_count
            print(f"⚠️ Rate limited (429). Retrying in {wait_time}s... (attempt {retry_count + 1}/3)")
            time.sleep(wait_time)
            return call_gemini_api(prompt, retry_count + 1)
        
        print(f"❌ Gemini API HTTP error: {e.code} - {error_body[:200]}")
        return None
    except Exception as e:
        print(f"❌ Gemini API error: {e}")
        return None


def build_owner_down_message(r):
    """Fixed template for DOWN message to owner - NO AI"""
    money = float(r.get("money_wasted_this_check") or 0)
    money_line = (
        f"\n💸 خسارة متوقعة من الإعلانات: <b>{money:.2f} جنيه</b> في الـ 5 دقايق دول"
        if money > 0 else ""
    )

    return (
        f"🚨 <b>موقعك وقع دلوقتي!</b>\n\n"
        f"🌐 <b>{safe(r.get('website_url'))}</b>\n"
        f"🕐 {safe(r.get('checked_at_cairo'))}"
        f"{money_line}\n\n"
        f"👨‍💻 اتصل بالمطور فوراً: <b>المطور اتبعتله تنبيه دلوقتي على تيليجرام.</b>"
    )


def build_dev_down_message(r):
    """Fixed template for DOWN message to developer - NO AI"""
    ping_detail = r.get("ping_detail", "[]")
    try:
        pings = json.loads(ping_detail) if isinstance(ping_detail, str) else ping_detail
        ping_lines = "\n".join([
            f"  Ping {i + 1}: {'✓' if p.get('success') else '✗'} | "
            f"{p.get('status_code', 'N/A')} | "
            f"{p.get('latency_ms', '—')}ms | "
            f"{p.get('error') or 'ok'}"
            for i, p in enumerate(pings)
        ])
    except Exception:
        ping_lines = str(ping_detail)

    return (
        f"🔴 <b>SITE DOWN</b>\n\n"
        f"<b>URL:</b> {safe(r.get('website_url'))}\n"
        f"<b>Final URL:</b> {safe(r.get('final_url'))}\n"
        f"<b>Time:</b> {safe(r.get('checked_at_cairo'))}\n"
        f"<b>HTTP Code:</b> {safe(r.get('last_status_code'))}\n"
        f"<b>Redirected:</b> {safe(r.get('was_redirected'))}\n\n"
        f"<b>── Latency ──</b>\n"
        f"Avg: {safe(r.get('avg_latency_ms'))} ms | "
        f"Min: {safe(r.get('min_latency_ms'))} ms | "
        f"Max: {safe(r.get('max_latency_ms'))} ms\n"
        f"DNS: {safe(r.get('dns_latency_ms'))} ms\n\n"
        f"<b>── Pings ──</b>\n"
        f"Failed: {safe(r.get('failed_pings'))} / {safe(r.get('total_pings'))}\n"
        f"{ping_lines}\n\n"
        f"<b>── SSL ──</b>\n"
        f"Valid: {safe(r.get('ssl_valid'))} | "
        f"Expires: {safe(r.get('ssl_expiry_date'))} ({safe(r.get('ssl_days_remaining'))} days)\n"
        f"Issuer: {safe(r.get('ssl_issuer'))}\n\n"
        f"<b>── Business ──</b>\n"
        f"Owner: {safe(r.get('user_telegram_id'))}\n"
        f"Ad Spend/mo: {safe(r.get('monthly_ad_spend'))} EGP\n"
        f"Wasted today: {safe(r.get('money_wasted_24h'))} EGP\n"
        f"Error: {safe(r.get('error_summary'))}"
    )


def build_owner_up_template(r):
    """Fixed template for UP message to owner - AI will add advice separately"""
    ssl_line = ""
    ssl_days = r.get("ssl_days_remaining")
    if ssl_days is not None and int(ssl_days) < 30:
        ssl_line = f"\n⚠️ تحذير: شهادة الأمان هتنتهي بعد {ssl_days} يوم!"

    return (
        f"✅ <b>موقعك رجع تاني!</b>\n\n"
        f"🌐 الموقع: {safe(r.get('website_url'))}\n"
        f"🕐 رجع الساعة: {safe(r.get('checked_at_cairo'))}\n\n"
        f"📊 <b>ملخص العطل:</b>\n"
        f"   وقت العطل : {safe(r.get('downtime_minutes_24h'))} دقيقة\n"
        f"   فلوس اتهدرت : {safe(r.get('money_wasted_24h'))} جنيه\n"
        f"   الاتاحة آخر ٢٤ ساعة: {safe(r.get('uptime_24h_pct'))}%\n\n"
        f"⚡ سرعة الموقع دلوقتي: {safe(r.get('speed_verdict'))}\n"
        f"🔒 شهادة SSL: {'سليمة ✅' if r.get('ssl_valid') else 'في مشكلة ❌'} — "
        f"تنتهي بعد {safe(r.get('ssl_days_remaining'))} يوم"
        f"{ssl_line}"
    )


def get_fixed_up_advice():
    """Fixed advice for UP message - used when AI is not available"""
    return (
        "• تواصل مع المطور بتاعك وراجع سبب العطل.\n"
        "• راجع طلبات العملاء اللي ممكن تكون فاتتك أثناء العطل.\n"
        "• طمّن عملاءك لو العطل أثر عليهم.\n"
        "• تابع أداء الموقع باستمرار عشان متخسرش مبيعات."
    )


def enrich_owner_up_message(filled_template, r):
    """
    Enrich the UP message with AI-generated business advice.
    This ONLY builds the message - does NOT send it.
    """
    
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY not set, using fixed UP advice")
        return filled_template + "\n\n── ماذا تفعل الآن؟ ──\n" + get_fixed_up_advice()
    
    # Extract comprehensive information for the AI
    website_url = r.get('website_url', 'Unknown')
    downtime_minutes = r.get('downtime_minutes_24h', 0)
    money_wasted_24h = r.get('money_wasted_24h', '0')
    money_wasted_this_check = r.get('money_wasted_this_check', '0')
    uptime_24h = r.get('uptime_24h_pct', '0')
    monthly_ad_spend = r.get('monthly_ad_spend', '0')
    speed_verdict = r.get('speed_verdict', 'Unknown')
    avg_latency = r.get('avg_latency_ms', 'N/A')
    success_rate = r.get('success_rate', 0)
    trend = r.get('trend', 'unknown')
    ssl_valid = r.get('ssl_valid', False)
    ssl_days = r.get('ssl_days_remaining', 'N/A')
    error_summary = r.get('error_summary', 'No specific error reported')
    
    # Build a rich prompt with all the data - but keep it concise
    prompt = (
        "أنت مستشار أعمال مصري خبير.\n"
        "صاحب موقع وقع عنده عطل ولسه الموقع رجع يشتغل تاني.\n"
        "اكتب 3 نصائح عملية بالعامية المصرية.\n\n"
        f"البيانات:\n"
        f"• الموقع: {website_url}\n"
        f"• مدة العطل: {downtime_minutes} دقيقة\n"
        f"• الاتاحة: {uptime_24h}%\n"
        f"• السرعة: {speed_verdict}\n"
        f"• الميزانية الإعلانية: {monthly_ad_spend} جنيه\n"
        f"• الخسائر اليوم: {money_wasted_24h} جنيه\n"
        f"• شهادة SSL: {'سليمة' if ssl_valid else 'فيها مشكلة'}\n\n"
        "اكتب 3 نصائح عملية بالعامية المصرية، كل نصيحة في سطر وتبدأ بـ •"
    )

    advice = call_gemini_api(prompt)
    
    if advice:
        # Clean up the advice
        advice = advice.strip()
        advice = advice.replace("── ماذا تفعل الآن؟ ──", "").strip()
        
        # Ensure each tip starts with •
        lines = advice.split('\n')
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if line:
                if not line.startswith('•'):
                    line = '• ' + line
                cleaned_lines.append(line)
        
        # If we have more than 3-4 lines, limit to 4
        if len(cleaned_lines) > 4:
            cleaned_lines = cleaned_lines[:4]
        
        advice = '\n'.join(cleaned_lines)
        
        return filled_template + "\n\n── ماذا تفعل الآن؟ ──\n" + advice
    else:
        print("⚠️ AI failed, using fixed UP advice")
        return filled_template + "\n\n── ماذا تفعل الآن؟ ──\n" + get_fixed_up_advice()


def handler(event, context):
    try:
        print(f"Full event received: {json.dumps(event, default=str)}")
        
        if "Records" in event:
            for record in event.get("Records", []):
                if "Sns" in record:
                    message = json.loads(record["Sns"]["Message"])
                    record = message
                elif "body" in record:
                    record = json.loads(record["body"])
        else:
            record = event
        
        print(f"Parsed record: {json.dumps(record, cls=DecimalEncoder, default=str)}")
        
        alert_event = record.get("alert_event", "DOWN")
        website_id = record.get("website_id")
        
        print(f"Alert event: {alert_event}")
        print(f"Website ID: {website_id}")

        # STEP 1: Fetch website metadata (contains Telegram IDs)
        website_info = None
        if website_id:
            website_info = fetch_website_info(website_id)
        
        owner_chat = ""
        dev_chat = ""
        
        if website_info:
            owner_chat = website_info.get("user_telegram_id", "")
            dev_chat = website_info.get("dev_telegram_id", "")
            print(f"✅ Owner chat: {owner_chat}")
            print(f"✅ Dev chat: {dev_chat}")
        else:
            print("⚠️ No website info found, checking event for chat IDs")
            owner_chat = record.get("user_telegram_id", "")
            dev_chat = record.get("dev_telegram_id", "")
        
        pings = fetch_latest_pings(website_id, limit=10)
        ping_analysis = analyze_multiple_pings(pings)
        
        combined_data = {}
        
        if website_info:
            combined_data.update(website_info)
        
        if ping_analysis:
            if ping_analysis['latest_ping']:
                combined_data.update(ping_analysis['latest_ping'])
            
            combined_data['total_pings_analyzed'] = ping_analysis['total_pings']
            combined_data['success_rate'] = ping_analysis['success_rate']
            combined_data['failed_pings_count'] = ping_analysis['failed']
            combined_data['avg_latency_analyzed'] = ping_analysis['avg_latency']
            combined_data['trend'] = ping_analysis['trend']
            combined_data['max_consecutive_failures'] = ping_analysis['max_consecutive_failures']
            combined_data['analysis_status'] = ping_analysis['latest_status']
        
        combined_data.update(record)
        
        if not combined_data.get("website_url") and website_info:
            combined_data["website_url"] = website_info.get("website_url")
        
        print(f"Combined data ready - URL: {combined_data.get('website_url')}")
        if ping_analysis:
            print(f"📊 Analyzed {ping_analysis['total_pings']} pings")
            print(f"📊 Success rate: {ping_analysis['success_rate']:.1f}%")
            print(f"📊 Trend: {ping_analysis['trend']}")

        if not combined_data.get("website_url"):
            print("❌ ERROR: No website_url in record")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing website_url in record"})
            }

        print(f"📢 Alert event: {alert_event} | URL: {combined_data.get('website_url')}")
        print(f"👤 Owner chat: {owner_chat}")
        print(f"👨‍💻 Dev chat: {dev_chat}")
        print(f"🔑 Gemini API Key set: {'Yes' if GEMINI_API_KEY else 'No'}")

        if alert_event == "DOWN":
            print("📤 Sending DOWN notifications...")
            
            # Send to developer - NO AI
            dev_msg = build_dev_down_message(combined_data)
            if dev_chat:
                print(f"📤 Sending dev DOWN message")
                send_telegram(dev_chat, dev_msg)
            else:
                print("⚠️ No dev chat ID, skipping dev notification")

            # Send to owner - NO AI
            owner_msg = build_owner_down_message(combined_data)
            if owner_chat:
                print(f"📤 Sending owner DOWN message")
                send_telegram(owner_chat, owner_msg)
            else:
                print("⚠️ No owner chat ID, skipping owner notification")

        elif alert_event == "UP":
            print("📤 Sending UP notifications...")
            
            # Build the UP message with AI advice
            template = build_owner_up_template(combined_data)
            owner_msg = enrich_owner_up_message(template, combined_data)
            
            # Send ONLY ONCE to owner
            if owner_chat:
                print(f"📤 Sending owner UP message with AI advice")
                send_telegram(owner_chat, owner_msg)
            else:
                print("⚠️ No owner chat ID, skipping owner notification")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "alert_event": alert_event,
                "website_url": combined_data.get("website_url"),
                "pings_analyzed": ping_analysis['total_pings'] if ping_analysis else 0,
                "success_rate": ping_analysis['success_rate'] if ping_analysis else 0,
                "trend": ping_analysis['trend'] if ping_analysis else "unknown",
                "gemini_used": bool(GEMINI_API_KEY) and alert_event == "UP",
                "sent_to": {
                    "owner": owner_chat if owner_chat else "Not sent",
                    "dev": dev_chat if alert_event == "DOWN" and dev_chat else "Not sent"
                }
            })
        }

    except Exception as e:
        print(f"❌ Alert Lambda error: {e}")
        import traceback
        traceback.print_exc()
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}