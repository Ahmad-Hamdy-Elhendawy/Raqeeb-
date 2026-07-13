import json
import boto3
import os
import urllib.request
import urllib.error
import urllib.parse
import socket
import ssl
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal

dynamodb      = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')

websites_table     = dynamodb.Table(os.environ['WEBSITES_TABLE'])
ping_results_table = dynamodb.Table(os.environ['PING_RESULTS_TABLE'])
ALERT_LAMBDA       = os.environ.get('ALERT_LAMBDA_NAME', '')

PING_COUNT      = 3
TIMEOUT_SEC     = 8
DOWN_THRESHOLD  = 2
CAIRO_TZ        = timezone(timedelta(hours=3))


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def safe_decimal(val):
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, int):
        return Decimal(val)
    return val


def to_cairo_time(dt_utc):
    return dt_utc.astimezone(CAIRO_TZ).strftime("%Y-%m-%d %H:%M:%S (Cairo)")


def get_previous_status(website_id):
    """Fetch the second-to-last ping to determine previous status."""
    try:
        resp = ping_results_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("website_id").eq(website_id),
            ScanIndexForward=False,
            Limit=2
        )
        items = resp.get("Items", [])
        if len(items) >= 2:
            return items[1].get("status", "UNKNOWN")
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def get_ssl_info(hostname):
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(
            socket.create_connection((hostname, 443), timeout=8),
            server_hostname=hostname
        ) as s:
            cert = s.getpeercert()
        expiry_str = cert.get("notAfter", "")
        expiry_dt  = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left  = (expiry_dt - datetime.now(timezone.utc)).days
        issuer     = dict(x[0] for x in cert.get("issuer", []))
        subject    = dict(x[0] for x in cert.get("subject", []))
        return {
            "ssl_valid":          True,
            "ssl_expiry_date":    expiry_dt.strftime("%Y-%m-%d"),
            "ssl_days_remaining": days_left,
            "ssl_issuer":         issuer.get("organizationName", "Unknown"),
            "ssl_domain":         subject.get("commonName", hostname),
            "ssl_warning":        days_left <= 30
        }
    except Exception as e:
        return {
            "ssl_valid":          False,
            "ssl_expiry_date":    None,
            "ssl_days_remaining": None,
            "ssl_issuer":         None,
            "ssl_domain":         hostname,
            "ssl_warning":        True,
            "ssl_error":          str(e)
        }


def get_dns_latency(hostname):
    try:
        start = time.time()
        socket.getaddrinfo(hostname, None)
        return round((time.time() - start) * 1000)
    except Exception:
        return None


def ping_url(url):
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SiteMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            latency_ms       = round((time.time() - start) * 1000)
            content          = resp.read()
            response_size_kb = round(len(content) / 1024, 1)
            final_url        = resp.url
            return {
                "success":          True,
                "status_code":      resp.status,
                "latency_ms":       latency_ms,
                "final_url":        final_url,
                "was_redirected":   final_url.rstrip("/") != url.rstrip("/"),
                "response_size_kb": response_size_kb,
                "error":            None
            }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.time() - start) * 1000)
        return {"success": False, "status_code": e.code, "latency_ms": latency_ms,
                "final_url": None, "was_redirected": False, "response_size_kb": None,
                "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        latency_ms = round((time.time() - start) * 1000)
        return {"success": False, "status_code": None, "latency_ms": latency_ms,
                "final_url": None, "was_redirected": False, "response_size_kb": None,
                "error": str(e)}


def calculate_money_wasted(monthly_ad_spend_str, downtime_minutes):
    try:
        monthly = float(monthly_ad_spend_str or 0)
        per_minute = monthly / (30 * 24 * 60)
        return round(per_minute * downtime_minutes, 2)
    except Exception:
        return 0.0


def get_uptime_stats(website_id):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        resp   = ping_results_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("website_id").eq(website_id)
                & boto3.dynamodb.conditions.Key("checked_at").gt(cutoff)
        )
        items  = resp.get("Items", [])
        if not items:
            return {"uptime_24h_pct": None, "downtime_minutes_24h": None}
        total  = len(items)
        up     = sum(1 for i in items if i.get("status") == "UP")
        return {
            "uptime_24h_pct":       round((up / total) * 100, 2),
            "downtime_minutes_24h": (total - up) * 5
        }
    except Exception:
        return {"uptime_24h_pct": None, "downtime_minutes_24h": None}


def process_website(website):
    url              = website["website_url"]
    website_id       = website["id"]
    monthly_ad_spend = website.get("monthly_ad_spend", "0")

    parsed   = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    is_https = parsed.scheme == "https"

    now_utc   = datetime.now(timezone.utc)
    now_cairo = to_cairo_time(now_utc)

    dns_latency_ms = get_dns_latency(hostname)
    ssl_info       = get_ssl_info(hostname) if is_https else {
        "ssl_valid": False, "ssl_expiry_date": None, "ssl_days_remaining": None,
        "ssl_issuer": None, "ssl_domain": hostname, "ssl_warning": True,
        "ssl_error": "Site is not using HTTPS"
    }

    pings = []
    for i in range(PING_COUNT):
        pings.append(ping_url(url))
        if i < PING_COUNT - 1:
            time.sleep(1)

    failed_count     = sum(1 for p in pings if not p["success"])
    successful_pings = [p for p in pings if p["success"]]

    avg_latency      = round(sum(p["latency_ms"] for p in successful_pings) / len(successful_pings)) if successful_pings else None
    min_latency      = min((p["latency_ms"] for p in successful_pings), default=None)
    max_latency      = max((p["latency_ms"] for p in successful_pings), default=None)
    avg_response_size = (
        round(sum(p["response_size_kb"] for p in successful_pings if p["response_size_kb"]) / len(successful_pings), 1)
        if successful_pings else None
    )

    status        = "DOWN" if failed_count >= DOWN_THRESHOLD else "UP"
    last_status_code = str(pings[-1].get("status_code") or "N/A")
    was_redirected   = any(p["was_redirected"] for p in successful_pings)
    final_url        = next((p["final_url"] for p in successful_pings if p["final_url"]), None)
    error_summary    = next((p["error"] for p in pings if p["error"]), None)

    if avg_latency is None:          speed_verdict = "Site unreachable"
    elif avg_latency < 500:          speed_verdict = "Fast"
    elif avg_latency < 1500:         speed_verdict = "Acceptable"
    elif avg_latency < 3000:         speed_verdict = "Slow — visitors may leave"
    else:                            speed_verdict = "Very slow — losing customers"

    uptime_stats = get_uptime_stats(website_id)
    money_wasted_this_check = calculate_money_wasted(monthly_ad_spend, 5) if status == "DOWN" else 0.0
    money_wasted_24h        = calculate_money_wasted(monthly_ad_spend, uptime_stats.get("downtime_minutes_24h") or 0)

    record = {
        "website_id":              website_id,
        "checked_at":              now_utc.isoformat(),
        "website_url":             url,
        "user_telegram_id":      website.get("user_telegram_id", ""),
        "dev_telegram_id":       website.get("dev_telegram_id", ""),
        "monthly_ad_spend":        monthly_ad_spend,
        "status":                  status,
        "last_status_code":        last_status_code,
        "checked_at_cairo":        now_cairo,
        "error_summary":           error_summary,
        "avg_latency_ms":          avg_latency,
        "min_latency_ms":          min_latency,
        "max_latency_ms":          max_latency,
        "dns_latency_ms":          dns_latency_ms,
        "speed_verdict":           speed_verdict,
        "avg_response_size_kb":    avg_response_size,
        "was_redirected":          was_redirected,
        "final_url":               final_url,
        "ssl_valid":               ssl_info.get("ssl_valid"),
        "ssl_expiry_date":         ssl_info.get("ssl_expiry_date"),
        "ssl_days_remaining":      ssl_info.get("ssl_days_remaining"),
        "ssl_issuer":              ssl_info.get("ssl_issuer"),
        "ssl_warning":             ssl_info.get("ssl_warning", False),
        "ssl_error":               ssl_info.get("ssl_error"),
        "is_https":                is_https,
        "money_wasted_this_check": str(money_wasted_this_check),
        "money_wasted_24h":        str(money_wasted_24h),
        "uptime_24h_pct":          str(uptime_stats.get("uptime_24h_pct") or ""),
        "downtime_minutes_24h":    uptime_stats.get("downtime_minutes_24h"),
        "failed_pings":            failed_count,
        "total_pings":             PING_COUNT,
        "ping_detail":             json.dumps(pings),
        "ttl":                     int(time.time()) + (7 * 24 * 60 * 60)
    }

    return {k: safe_decimal(v) for k, v in record.items() if v is not None}


def trigger_alert(record, event_type):
    """Async invoke the alert Lambda. Non-blocking."""
    if not ALERT_LAMBDA:
        return
    try:
        payload = json.loads(json.dumps(record, cls=DecimalEncoder))
        payload["alert_event"] = event_type   # "DOWN" or "UP"
        lambda_client.invoke(
            FunctionName   = ALERT_LAMBDA,
            InvocationType = 'Event',
            Payload        = json.dumps(payload).encode()
        )
        print(f"Alert triggered: {event_type} → {record.get('website_url')}")
    except Exception as e:
        print(f"Failed to trigger alert: {e}")


def handler(event, context):
    print("Starting ping cycle")

    websites = websites_table.scan().get("Items", [])
    if not websites:
        return {"statusCode": 200, "body": "No websites to ping."}

    results = []
    errors  = []

    for website in websites:
        try:
            record = process_website(website)

            # ── Transition detection ──────────────────────────
            prev_status = get_previous_status(website["id"])
            curr_status = record["status"]

            # Save AFTER reading previous status
            ping_results_table.put_item(Item=record)

            if prev_status != "DOWN" and curr_status == "DOWN":
                # Transition: UP → DOWN
                trigger_alert(record, "DOWN")
            elif prev_status == "DOWN" and curr_status == "UP":
                # Transition: DOWN → UP
                trigger_alert(record, "UP")
            # else: no transition → silence

            results.append({
                "url":    record["website_url"],
                "status": curr_status,
                "prev":   prev_status,
                "avg_ms": record.get("avg_latency_ms")
            })
            print(f"[{curr_status}] {record['website_url']} (was {prev_status})")

        except Exception as e:
            errors.append({"url": website.get("website_url"), "error": str(e)})
            print(f"ERROR: {website.get('website_url')} → {e}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "checked": len(results),
            "up":      sum(1 for r in results if r["status"] == "UP"),
            "down":    sum(1 for r in results if r["status"] == "DOWN"),
            "errors":  len(errors),
            "results": results
        }, cls=DecimalEncoder)
    }
