import json
import boto3
import uuid
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['TABLE_NAME'])

def handler(event, context):
    headers = {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST,OPTIONS"
    }

    if event.get('httpMethod') == 'OPTIONS':
        return {"statusCode": 200, "headers": headers, "body": ""}

    try:
        body = json.loads(event.get('body', '{}'))

        user_telegram_id = body.get('user_telegram_id')
        dev_telegram_id  = body.get('dev_telegram_id')
        website_url      = body.get('website_url')
        monthly_ad_spend = body.get('monthly_ad_spend')

        if not all([user_telegram_id, dev_telegram_id, website_url, monthly_ad_spend]):
            return {
                "statusCode": 400,
                "headers": headers,
                "body": json.dumps({"error": "Missing required fields"})
            }

        item = {
            "id":               str(uuid.uuid4()),
            "user_telegram_id": str(user_telegram_id),
            "dev_telegram_id":  str(dev_telegram_id),
            "website_url":      website_url,
            "monthly_ad_spend": str(monthly_ad_spend),
            "created_at":       datetime.utcnow().isoformat()
        }

        table.put_item(Item=item)

        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({"message": "Website registered successfully", "id": item["id"]})
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": headers,
            "body": json.dumps({"error": str(e)})
        }
