from fastapi import APIRouter, Depends, HTTPException, Request, status
import httpx
from db.supabase_client import get_settings, get_supabase_admin_client
from models.schemas import UserResponse
from services.auth_service import require_current_user
from services.logging import get_logger

logger = get_logger("vent.payments")
router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/create-checkout")
async def create_checkout_session(
    user: UserResponse = Depends(require_current_user),
):
    settings = get_settings()
    if not settings.dodo_payments_api_key or not settings.dodo_payments_product_id:
        raise HTTPException(
            status_code=500,
            detail="Payments are not configured on the server. Please check environment variables."
        )

    # Detect base url based on api key
    is_live = settings.dodo_payments_api_key.startswith("live_") or "live" in settings.dodo_payments_api_key.lower()
    base_url = "https://live.dodopayments.com" if is_live else "https://test.dodopayments.com"

    # Find production frontend origin if possible
    frontend_url = "https://vent.entrext.com"
    if settings.allowed_origins:
        prod_origins = [o for o in settings.allowed_origins if "localhost" not in o and "127.0.0.1" not in o]
        if prod_origins:
            frontend_url = prod_origins[0]
        else:
            frontend_url = settings.allowed_origins[0]

    headers = {
        "Authorization": f"Bearer {settings.dodo_payments_api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "product_cart": [
            {
                "product_id": settings.dodo_payments_product_id,
                "quantity": 1
            }
        ],
        "customer": {
            "email": user.email,
            "name": user.email.split("@")[0] if user.email else "User"
        },
        "return_url": f"{frontend_url}/dashboard?payment=success",
        "cancel_url": f"{frontend_url}/settings?payment=cancelled",
        "metadata": {
            "user_id": user.id
        }
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{base_url}/v1/checkouts",
                headers=headers,
                json=payload,
            )
            if response.status_code != 200 and response.status_code != 201:
                logger.error("Dodo payments session creation failed: %s %s", response.status_code, response.text)
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to create checkout session: {response.text}"
                )
            
            data = response.json()
            return {"checkout_url": data.get("checkout_url")}
    except Exception as e:
        logger.error("Error creating Dodo payment session: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail="Error communicating with payment gateway."
        )


@router.post("/webhook")
async def dodo_webhook(request: Request):
    settings = get_settings()
    body = await request.body()
    body_str = body.decode("utf-8")
    
    # Secure Timing-Attack-Safe Signature Verification
    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    signature_header = request.headers.get("webhook-signature") or request.headers.get("x-webhook-signature")

    if settings.dodo_payments_webhook_secret and signature_header and webhook_id and webhook_timestamp:
        import hmac
        import hashlib
        
        signatures = []
        if "," in signature_header:
            parts = signature_header.split(",")
            for p in parts:
                if p.startswith("v1,"):
                    signatures.append(p[3:])
                elif "=" in p:
                    signatures.append(p.split("=")[1])
                else:
                    signatures.append(p)
        else:
            if signature_header.startswith("v1,"):
                signatures.append(signature_header[3:])
            else:
                signatures.append(signature_header)

        verified = False
        message = f"{webhook_id}.{webhook_timestamp}.{body_str}"
        secret_key = settings.dodo_payments_webhook_secret

        for sig in signatures:
            computed = hmac.new(
                key=secret_key.encode("utf-8"),
                msg=message.encode("utf-8"),
                digestmod=hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(computed, sig):
                verified = True
                break

            # Try decoding base64 if standard webhooks secret
            try:
                import base64
                decoded_secret = base64.b64decode(secret_key.replace("whsec_", ""))
                computed_b64 = hmac.new(
                    key=decoded_secret,
                    msg=message.encode("utf-8"),
                    digestmod=hashlib.sha256
                ).hexdigest()
                if hmac.compare_digest(computed_b64, sig):
                    verified = True
                    break
            except Exception:
                pass

        if not verified:
            logger.error("Dodo webhook signature verification failed!")
            raise HTTPException(status_code=401, detail="Invalid signature")
        else:
            logger.info("Dodo webhook signature verified successfully!")

    # Parse JSON body
    try:
        import json
        event = json.loads(body_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = event.get("type")
    data = event.get("data", {})
    
    logger.info("Received Dodo Payments Webhook: type=%s", event_type)

    # Identify user to upgrade or downgrade
    user_id = data.get("metadata", {}).get("user_id") or event.get("metadata", {}).get("user_id")
    customer_email = data.get("customer", {}).get("email") or event.get("customer", {}).get("email")

    admin_client = get_supabase_admin_client()
    target_user_id = None

    if user_id:
        target_user_id = user_id
    elif customer_email:
        # Resolve user_id from profiles table by email
        try:
            profile_res = admin_client.table("profiles").select("id").eq("email", customer_email).execute()
            if profile_res.data:
                target_user_id = profile_res.data[0]["id"]
        except Exception as e:
            logger.error("Failed to resolve user by email: %s", str(e))

    if not target_user_id:
        logger.warning("Could not identify target user for webhook event: %s", event_type)
        return {"status": "ignored", "reason": "user not found"}

    if event_type in ["checkout.session.completed", "subscription.created", "subscription.active"]:
        # Upgrade user
        try:
            admin_client.table("profiles").update({"is_premium": True}).eq("id", target_user_id).execute()
            logger.info("Upgraded user %s to Premium!", target_user_id)
        except Exception as e:
            logger.error("Failed to upgrade user in database: %s", str(e))
            raise HTTPException(status_code=500, detail="Database update failed")
            
    elif event_type in ["subscription.cancelled", "subscription.expired", "subscription.past_due"]:
        # Downgrade user
        try:
            admin_client.table("profiles").update({"is_premium": False}).eq("id", target_user_id).execute()
            logger.info("Downgraded user %s to Free", target_user_id)
        except Exception as e:
            logger.error("Failed to downgrade user in database: %s", str(e))
            raise HTTPException(status_code=500, detail="Database update failed")

    return {"status": "success"}

