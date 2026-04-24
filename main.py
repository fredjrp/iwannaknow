from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
import httpx

app = FastAPI(
    title="Delo Backend API",
    description="Server-Driven UI backend for Delo - Group Buy Logistics Platform",
    version="2.0.0"
)

# ─── CORS ──────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── MODELS ────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = []

class DealRequest(BaseModel):
    product_name: str
    wholesale_price: float
    target_moq: int
    current_members: int = 0
    jumia_selling_price: Optional[float] = None

class StkPushRequest(BaseModel):
    phone_number: str
    amount: float
    deal_id: Optional[str] = None
    group_buy_id: Optional[str] = None

class JumiaOrderRequest(BaseModel):
    api_key: str
    order_id: str
    action: str  # "get_label", "mark_ready", "get_pending"

class HubRequest(BaseModel):
    name: str
    address: str
    latitude: float
    longitude: float
    phone_number: Optional[str] = None

# ─── LAYOUT ENDPOINTS (SDUI Engine) ────────

@app.get("/layout/{screen_name}")
async def get_layout(screen_name: str, user_role: str = "ordinary"):
    """
    Serve UI JSON for the Flutter thin client.
    Different layouts can be served based on user role.
    """
    # Check for role-specific layout first
    role_file = f"layouts/{screen_name}_{user_role}.json"
    generic_file = f"layouts/{screen_name}.json"
    
    if os.path.exists(role_file):
        with open(role_file, "r") as f:
            layout = json.load(f)
    elif os.path.exists(generic_file):
        with open(generic_file, "r") as f:
            layout = json.load(f)
    else:
        return {"error": "Layout not found", "status": 404}
    
    # Inject dynamic data (group buy counts, etc.)
    if os.path.exists("data/deals.json"):
        with open("data/deals.json", "r") as f:
            deals_data = json.load(f)
        layout["_dynamic_data"] = {
            "active_deals": len(deals_data.get("deals", [])),
            "hubs_count": 5,
            "total_users": 128
        }
    
    return layout

@app.get("/layout/components/{component_name}")
async def get_component(component_name: str):
    """Fetch a single UI component by name"""
    file_path = f"components/{component_name}.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return {"error": "Component not found", "status": 404}

# ─── DEALS ENDPOINTS ───────────────────────

@app.get("/api/deals")
async def get_active_deals(hub: Optional[str] = None, category: Optional[str] = None):
    """Return active group buy deals with optional filters"""
    if not os.path.exists("data/deals.json"):
        return {"deals": [], "count": 0}
    
    with open("data/deals.json", "r") as f:
        data = json.load(f)
    
    deals = data.get("deals", [])
    
    # Filter by hub
    if hub:
        deals = [d for d in deals if d.get("hub") == hub]
    
    # Filter by category
    if category:
        deals = [d for d in deals if d.get("category") == category]
    
    return {
        "deals": deals,
        "count": len(deals),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/deals/{deal_id}")
async def get_deal_detail(deal_id: str):
    """Get detailed info for a specific deal"""
    if not os.path.exists("data/deals.json"):
        return {"error": "Deal not found", "status": 404}
    
    with open("data/deals.json", "r") as f:
        data = json.load(f)
    
    for deal in data.get("deals", []):
        if deal["id"] == deal_id:
            # Add profit calculation for Jumia sellers
            wholesale = deal["wholesale_price"]
            retail = deal.get("retail_price", wholesale * 1.8)
            jumia_commission = retail * 0.10
            shipping = 150
            net_profit = retail - wholesale - jumia_commission - shipping
            margin = (net_profit / retail) * 100
            
            deal["profit_analysis"] = {
                "landing_cost": wholesale,
                "estimated_retail": retail,
                "jumia_commission": round(jumia_commission, 2),
                "shipping": shipping,
                "net_profit": round(net_profit, 2),
                "margin_percent": round(margin, 1),
                "verdict": "profitable" if margin > 20 else "low_margin" if margin > 10 else "not_recommended"
            }
            return deal
    
    return {"error": "Deal not found", "status": 404}

@app.post("/api/deals/calculate-profit")
async def calculate_profit(request: DealRequest):
    """Calculate potential profit for a Jumia seller"""
    retail = request.jumia_selling_price or (request.wholesale_price * 1.8)
    jumia_commission = retail * 0.10
    shipping = 150
    net_profit = retail - request.wholesale_price - jumia_commission - shipping
    margin = (net_profit / retail) * 100
    
    # Calculate savings from group buy vs individual purchase
    individual_price = request.wholesale_price * 1.3  # 30% markup for individual
    group_savings = individual_price - request.wholesale_price
    
    return {
        "wholesale_price": request.wholesale_price,
        "individual_price": round(individual_price, 2),
        "group_buy_price": request.wholesale_price,
        "savings_per_unit": round(group_savings, 2),
        "estimated_retail": round(retail, 2),
        "jumia_commission": round(jumia_commission, 2),
        "shipping": shipping,
        "net_profit": round(net_profit, 2),
        "margin_percent": round(margin, 1),
        "recommendation": "strong_buy" if margin > 25 else "good_deal" if margin > 15 else "low_margin",
        "roi": f"{round((net_profit / request.wholesale_price) * 100, 1)}%"
    }

# ─── AI ENDPOINTS (OpenRouter Proxy) ───────

@app.post("/api/ai/chat")
async def chat_with_floy(request: ChatRequest):
    """Proxy to OpenRouter - hides API key from client"""
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    
    if not openrouter_key:
        return {"reply": "Floy is recharging her battery! Try again in a moment. 🔋"}

    # Load deals context for Floy
    deals_context = ""
    if os.path.exists("data/deals.json"):
        with open("data/deals.json", "r") as f:
            deals_data = json.load(f)
        active_deals = [d for d in deals_data.get("deals", []) if d.get("status") == "active"]
        deals_context = f"Current active deals: {json.dumps(active_deals[:3])}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "HTTP-Referer": "https://delo.app",
                    "X-Title": "Delo - Floy Assistant",
                },
                json={
                    "model": "nvidia/llama-3.1-nemotron-nano-8b-v1:free",
                    "messages": [
                        {
                            "role": "system",
                            "content": f"""You are Floy, the AI assistant for Delo - a group-buy logistics platform in Kenya.

PERSONALITY:
- Energetic, friendly, and street-smart
- Use Kenyan slang occasionally (safi, mzuri, poa, wazi, nyamaza)
- Keep responses short (2-4 sentences max)
- Always end with an emoji or question

RULES:
- Help users understand group buys and calculate savings
- Know about Jumia integration for dropshippers
- Collection hubs are in: CBD, Westlands, Kilimani, Eastleigh, Karen
- Payment is via M-Pesa STK Push

{deals_context}

NEVER mention being an AI. You are Floy, a real Kenyan assistant."""
                        },
                        *request.history if request.history else [],
                        {"role": "user", "content": request.message}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 250,
                },
                timeout=15.0,
            )
            
            data = response.json()
            reply = data["choices"][0]["message"]["content"]
            return {"reply": reply, "model": data.get("model", "unknown")}
            
        except httpx.TimeoutException:
            # Try fallback model
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {openrouter_key}",
                        "HTTP-Referer": "https://delo.app",
                    },
                    json={
                        "model": "qwen/qwen-2.5-7b-instruct:free",
                        "messages": [
                            {"role": "system", "content": "You are Floy, a friendly Kenyan AI assistant. Keep responses short and use Swahili slang."},
                            {"role": "user", "content": request.message}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 200,
                    },
                    timeout=10.0,
                )
                data = response.json()
                reply = data["choices"][0]["message"]["content"]
                return {"reply": f"{reply} (using backup brain 🧠)", "model": "fallback"}
            except:
                return {"reply": "Eish! I got stuck in Nairobi jam. Try again soon? 🚗💨", "model": "none"}
                
        except Exception as e:
            return {"reply": f"Oops! Network issues. Maybe try 'How do group buys work?' 🤔", "model": "error"}

@app.get("/api/ai/suggestions")
async def get_ai_suggestions():
    """Return suggested prompts for Floy"""
    return {
        "suggestions": [
            "How do group buys work on Delo?",
            "Calculate profit for a power bank deal",
            "Where are the collection hubs in Nairobi?",
            "How do I connect my Jumia account?",
            "What's the best deal right now?",
            "How much can I save buying in a group?"
        ]
    }

# ─── HUB ENDPOINTS ─────────────────────────

@app.get("/api/hubs")
async def get_hubs():
    """Return all collection hubs"""
    if os.path.exists("data/hubs.json"):
        with open("data/hubs.json", "r") as f:
            return json.load(f)
    return {"hubs": []}

@app.post("/api/hubs/suggest")
async def suggest_hub(request: HubRequest):
    """Users can suggest new hub locations"""
    suggestion = request.dict()
    suggestion["status"] = "suggested"
    suggestion["suggested_at"] = datetime.now().isoformat()
    
    # In production, save to database
    # For now, append to a file
    file_path = "data/hub_suggestions.json"
    existing = []
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            existing = json.load(f)
    existing.append(suggestion)
    with open(file_path, "w") as f:
        json.dump(existing, f, indent=2)
    
    return {"message": "Hub suggestion received! We'll review it.", "suggestion": suggestion}

# ─── M-PESA PROXY ENDPOINTS ────────────────

@app.post("/api/mpesa/stk-push")
async def trigger_stk_push(request: StkPushRequest):
    """
    Proxy for M-Pesa STK Push.
    In production, this calls Safaricom's API directly.
    For development, returns mock response.
    """
    # Format phone number
    phone = request.phone_number.replace("+", "").replace(" ", "")
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    elif not phone.startswith("254"):
        phone = "254" + phone
    
    # Mock STK Push response (replace with actual Safaricom API call in production)
    checkout_id = f"ws_CO_{datetime.now().strftime('%Y%m%d%H%M%S')}_{phone[-4:]}"
    
    return {
        "success": True,
        "message": f"STK Push sent to {phone}. Check your phone and enter PIN.",
        "checkout_request_id": checkout_id,
        "merchant_request_id": f"MR_{checkout_id}",
        "amount": request.amount,
        "phone_number": phone,
        "timestamp": datetime.now().isoformat(),
        "note": "This is a MOCK response. Connect to Safaricom API for production."
    }

@app.post("/api/mpesa/callback")
async def mpesa_callback(request: dict):
    """Handle M-Pesa payment confirmation callback"""
    # In production, verify signature and process payment
    result = request.get("Body", {}).get("stkCallback", {})
    
    callback_data = {
        "merchant_request_id": result.get("MerchantRequestID"),
        "checkout_request_id": result.get("CheckoutRequestID"),
        "result_code": result.get("ResultCode"),
        "result_desc": result.get("ResultDesc"),
        "processed_at": datetime.now().isoformat()
    }
    
    # Log to file (in production, update database)
    os.makedirs("logs", exist_ok=True)
    with open("logs/mpesa_callbacks.jsonl", "a") as f:
        f.write(json.dumps(callback_data) + "\n")
    
    return {"status": "received", "message": "Callback processed"}

# ─── JUMIA PROXY ENDPOINTS ─────────────────

@app.post("/api/jumia/orders")
async def proxy_jumia_orders(request: JumiaOrderRequest):
    """
    Proxy for Jumia Seller Center API calls.
    In production, this connects to Jumia's actual API.
    """
    # NEVER log the API key
    print(f"Jumia API call: action={request.action}, order={request.order_id}")
    
    if request.action == "get_pending":
        # Mock response
        return {
            "orders": [
                {
                    "order_id": "JU-2026-0042",
                    "product": "10,000mAh Power Bank",
                    "quantity": 5,
                    "status": "pending",
                    "customer_name": "Jane M.",
                    "created_at": "2026-04-20T10:30:00Z"
                }
            ]
        }
    elif request.action == "get_label":
        return {
            "order_id": request.order_id,
            "label_url": f"https://delo.app/labels/{request.order_id}.pdf",
            "tracking_number": f"JM{request.order_id[-6:]}KE"
        }
    elif request.action == "mark_ready":
        return {
            "order_id": request.order_id,
            "status": "ready_to_ship",
            "updated_at": datetime.now().isoformat()
        }
    
    return {"error": "Invalid action", "status": 400}

@app.get("/api/jumia/fee-structure")
async def get_jumia_fees():
    """Return current Jumia fee structure for profit calculations"""
    return {
        "categories": {
            "electronics": {"commission": 10, "shipping": 150},
            "fashion": {"commission": 15, "shipping": 100},
            "home": {"commission": 12, "shipping": 200},
            "beauty": {"commission": 10, "shipping": 120}
        },
        "vat": 16,
        "updated": "2026-04-01"
    }

# ─── HEALTH & META ─────────────────────────

@app.get("/")
async def health_check():
    return {
        "status": "Delo Backend is Active",
        "version": "2.0.0",
        "environment": os.getenv("RENDER", "development"),
        "endpoints": {
            "layouts": "/layout/{screen_name}",
            "deals": "/api/deals",
            "ai_chat": "/api/ai/chat",
            "hubs": "/api/hubs",
            "mpesa": "/api/mpesa/stk-push",
            "jumia": "/api/jumia/orders",
            "profit_calc": "/api/deals/calculate-profit"
        },
        "components_available": [
            "M3Card", "M3SplitButton", "M3Badge", "M3Chip",
            "M3NavigationBar", "M3NavigationRail", "M3MapboxMap",
            "M3FilledButton", "M3OutlinedButton", "M3Switch",
            "M3Tooltip", "M3Snackbar", "M3ProgressIndicator",
            "PaymentSheet", "FloyChatDrawer", "HubMarker",
            "HubDetailSheet", "ProfitCalculator"
        ]
    }

@app.get("/api/version")
async def get_version():
    return {
        "version": "2.0.0",
        "min_client_version": "1.5.0",
        "force_update": False,
        "release_notes": "Added Floy AI, Jumia integration, M-Pesa STK Push, and Mapbox hub markers"
    }
