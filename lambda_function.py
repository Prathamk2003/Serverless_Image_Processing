import os
import json
import uuid
import base64
import boto3
import requests
from io import BytesIO
from cgi import FieldStorage

# Environment variables
TWILIO_SID       = os.environ['TWILIO_SID']
TWILIO_AUTH      = os.environ['TWILIO_AUTH_TOKEN']
FROM_NUMBER      = os.environ['FROM_NUMBER']
OPENAI_API_KEY   = os.environ['OPENAI_API_KEY']
S3_BUCKET        = os.environ['S3_BUCKET_NAME']
OPENAI_ENDPOINT  = "https://api.openai.com/v1/chat/completions"

s3 = boto3.client('s3')


def lambda_handler(event, context):
    try:
        print("Event:", json.dumps(event)[:1000])  # Log part of event for debugging

        # Decode body if base64
        body = event.get('body', '')
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body)
        else:
            body = body.encode('utf-8')

        # Normalize headers for case-insensitive access
        headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
        content_type = headers.get("content-type")
        if not content_type:
            raise ValueError("Missing Content-Type header.")

        # Parse multipart/form-data (Twilio format)
        form = FieldStorage(
            fp=BytesIO(body),
            environ={'REQUEST_METHOD': 'POST'},
            headers={'content-type': content_type}
        )

        media_url = form.getvalue("MediaUrl0")
        user_whats = form.getvalue("From")

        if not media_url or not user_whats:
            raise ValueError("Missing MediaUrl0 or From in webhook.")

        print("→ Media URL:", media_url)

        # 1. Download image
        try:
            img_resp = requests.get(media_url, auth=(TWILIO_SID, TWILIO_AUTH), timeout=10)
            img_resp.raise_for_status()
            data = img_resp.content
            print("→ Image downloaded.")
        except Exception as err:
            print("× Failed to download image:", str(err))
            return {"statusCode": 500, "body": json.dumps({"error": "Failed to download image."})}

        # 2. Upload to S3
        key = f"uploads/{uuid.uuid4().hex}.jpg"
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType="image/jpeg")
        print("→ Stored to S3:", key)

        # 3. Call OpenAI Vision
        diagnosis = call_openai(data)
        print("→ OpenAI diagnosis:", diagnosis)

        # 4. Send WhatsApp reply
        send_success = send_whatsapp(user_whats, diagnosis)
        if not send_success:
            print("× Failed to send WhatsApp reply.")

        return {
            "statusCode": 200,
           # "body": json.dumps({"message": "ok"})
        }

    except Exception as e:
        print("× Error in lambda_handler:", str(e))
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


def call_openai(image_bytes: bytes) -> str:
    try:
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "Diagnose the plant disease and suggest a remedy."}
                ]}
            ],
            "max_tokens": 400
        }

        response = requests.post(OPENAI_ENDPOINT, headers=headers, json=payload, timeout=20)
        print("→ OpenAI raw response:", response.text)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("× Error calling OpenAI:", str(e))
        return "Could not analyze the image at the moment. Please try again later."


def send_whatsapp(to: str, body: str) -> bool:
    try:
        payload = {
            "To": to,
            "From": FROM_NUMBER,
            "Body": body
        }
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data=payload,
            auth=(TWILIO_SID, TWILIO_AUTH),
            timeout=10
        )
        resp.raise_for_status()
        print("→ WhatsApp message SID:", resp.json().get("sid"))
        return True
    except Exception as e:
        print("× WhatsApp send failed:", str(e))
        return False
