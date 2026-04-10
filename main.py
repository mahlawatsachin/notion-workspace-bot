import os
import json
import requests
import telebot
from telebot import types

# === KEYS FROM ENV VARIABLES ===
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
PARENT_PAGE_ID = os.environ['PARENT_PAGE_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

# === CONVERSATION STATE ===
user_sessions = {}

# === NOTION API ===
NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
    }

def create_page(parent_id, title):
    data = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]}
        }
    }
    res = requests.post(f"{NOTION_BASE}/pages", headers=notion_headers(), json=data)
    res.raise_for_status()
    return res.json()

def create_database(parent_id, title, properties):
    data = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties
    }
    res = requests.post(f"{NOTION_BASE}/databases", headers=notion_headers(), json=data)
    res.raise_for_status()
    return res.json()

def update_database(db_id, properties):
    data = {"properties": properties}
    res = requests.patch(f"{NOTION_BASE}/databases/{db_id}", headers=notion_headers(), json=data)
    res.raise_for_status()
    return res.json()

# === GROQ API ===
def call_groq(prompt, system_msg=None):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": prompt})
    
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 2000
    }
    res = requests.post(url, headers=headers, json=data, timeout=60)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]

# === CONVERSATION FLOW ===
def analyze_requirement(user_input):
    """AI analyzes and asks clarifying questions"""
    system = """You are a Notion workspace consultant. User wants to build a workspace. 
    Analyze their requirement and ask 2-3 clarifying questions to understand:
    - What data they'll track
    - Their workflow
    - Team size
    - Key use cases
    
    Respond in Hinglish (Hindi+English mix) in a friendly tone. Keep it short and conversational."""
    
    prompt = f"""User ne kaha: \"{user_input}\"
    
Analyze karke 2-3 important questions pucho taaki workspace perfectly ban sake."""
    
    return call_groq(prompt, system)

def generate_recommendation(conversation_history):
    """AI gives opinion on ideal workspace structure"""
    system = """You are a Notion workspace architect. Based on conversation, recommend the best workspace structure.
    Give opinion on:
    - Which databases to create
    - Key properties and relationships
    - Why this structure will work
    
    Respond in Hinglish, be opinionated but helpful. End with asking for confirmation."""
    
    prompt = f"""Conversation:
{conversation_history}

Now give your detailed recommendation for the ideal Notion workspace structure."""
    
    return call_groq(prompt, system)

def generate_workspace_spec(conversation_history):
    """Generate JSON spec from conversation"""
    system = "You are a Notion workspace architect. Always respond with valid JSON only, no markdown, no explanation."
    
    prompt = f"""Based on this conversation:
{conversation_history}

Create a Notion workspace spec. Return ONLY this JSON structure (no markdown):
{{
  "workspace_name": "Name Here",
  "databases": [
    {{
      "name": "Database Name",
      "properties": [
        {{"name": "Status", "type": "select", "options": ["Active", "Done"]}},
        {{"name": "Priority", "type": "select", "options": ["High", "Medium", "Low"]}},
        {{"name": "Due Date", "type": "date"}},
        {{"name": "Notes", "type": "rich_text"}},
        {{"name": "Amount", "type": "number"}}
      ],
      "relations": [
        {{"property_name": "Related DB", "related_database": "Other Database Name"}}
      ]
    }}
  ]
}}

Rules:
- 3-6 databases based on conversation
- 4-8 properties per database
- Property types: title(auto), select, multi_select, date, number, rich_text, checkbox, url, email, phone_number
- Relations only between databases in this spec
- No extra text, just JSON"""
    
    raw = call_groq(prompt, system)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)

# === WORKSPACE BUILDER ===
def build_properties_no_relations(db_spec):
    props = {"Name": {"title": {}}}
    for prop in db_spec.get("properties", []):
        ptype = prop["type"]
        pname = prop["name"]
        if pname.lower() == "name":
            continue
        if ptype == "select":
            options = [{"name": o, "color": "default"} for o in prop.get("options", ["Option 1"])]
            props[pname] = {"select": {"options": options}}
        elif ptype == "multi_select":
            options = [{"name": o, "color": "default"} for o in prop.get("options", ["Option 1"])]
            props[pname] = {"multi_select": {"options": options}}
        elif ptype == "date":
            props[pname] = {"date": {}}
        elif ptype == "number":
            props[pname] = {"number": {"format": "number"}}
        elif ptype == "rich_text":
            props[pname] = {"rich_text": {}}
        elif ptype == "checkbox":
            props[pname] = {"checkbox": {}}
        elif ptype == "url":
            props[pname] = {"url": {}}
        elif ptype == "email":
            props[pname] = {"email": {}}
        elif ptype == "phone_number":
            props[pname] = {"phone_number": {}}
    return props

def build_relations(db_spec, db_name_to_id):
    props = {}
    for rel in db_spec.get("relations", []):
        related_name = rel.get("related_database", "")
        if related_name in db_name_to_id:
            pname = rel.get("property_name", f"Link to {related_name}")
            props[pname] = {
                "relation": {
                    "database_id": db_name_to_id[related_name],
                    "single_property": {}
                }
            }
    return props

def build_workspace(spec):
    workspace_name = spec.get("workspace_name", "My Workspace")
    root_page = create_page(PARENT_PAGE_ID, workspace_name)
    root_page_id = root_page["id"]
    root_url = root_page["url"]
    
    db_name_to_id = {}
    db_urls = {}
    
    for db in spec["databases"]:
        props = build_properties_no_relations(db)
        db_res = create_database(root_page_id, db["name"], props)
        db_name_to_id[db["name"]] = db_res["id"]
        db_urls[db["name"]] = db_res["url"]
    
    for db in spec["databases"]:
        rel_props = build_relations(db, db_name_to_id)
        if rel_props:
            try:
                update_database(db_name_to_id[db["name"]], rel_props)
            except:
                pass
    
    return {"root_url": root_url, "db_urls": db_urls}

# === TELEGRAM BOT ===
bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    bot.reply_to(message,
        "🚀 *Steamtech Notion Workspace Builder*\n\n"
        "Main tumhara Notion workspace consultant hoon!\n\n"
        "Bas mujhe batao kya banana hai, main:\n"
        "✅ Cross-questions karke clarify karunga\n"
        "✅ Best structure recommend karunga\n"
        "✅ Phir automatically Notion mein build kar dunga\n\n"
        "Example:\n"
        "_\"Dal mill clients ke liye CRM banana hai\"_",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: True)
def handle(message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    
    # Initialize session if new
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "stage": "initial",
            "history": []
        }
    
    session = user_sessions[user_id]
    session["history"].append(f"User: {user_text}")
    
    try:
        if session["stage"] == "initial":
            # Stage 1: Ask clarifying questions
            bot.reply_to(message, "🤔 Samajh raha hoon... ek minute")
            questions = analyze_requirement(user_text)
            session["history"].append(f"Bot: {questions}")
            session["stage"] = "clarifying"
            bot.send_message(message.chat.id, questions)
        
        elif session["stage"] == "clarifying":
            # Stage 2: Give recommendation
            bot.reply_to(message, "💡 Analysis kar raha hoon...")
            conversation = "\n".join(session["history"])
            recommendation = generate_recommendation(conversation)
            session["history"].append(f"Bot: {recommendation}")
            session["stage"] = "recommending"
            
            # Add confirmation buttons
            markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
            markup.add("✅ Haan, build karo", "❌ Nahi, change karo")
            bot.send_message(message.chat.id, recommendation, reply_markup=markup)
        
        elif session["stage"] == "recommending":
            if "haan" in user_text.lower() or "yes" in user_text.lower() or "build" in user_text.lower():
                # Stage 3: Build workspace
                bot.reply_to(message, "🏗️ Building workspace... 20-30 seconds lagenge", reply_markup=types.ReplyKeyboardRemove())
                conversation = "\n".join(session["history"])
                spec = generate_workspace_spec(conversation)
                result = build_workspace(spec)
                
                msg = "✅ *Workspace Ban Gaya!*\n\n"
                msg += f"📄 Root Page: {result['root_url']}\n\n"
                msg += "📊 Databases:\n"
                for name, url in result['db_urls'].items():
                    msg += f"  • [{name}]({url})\n"
                
                bot.send_message(message.chat.id, msg, parse_mode="Markdown")
                # Reset session
                del user_sessions[user_id]
            else:
                # User wants changes
                session["stage"] = "clarifying"
                bot.send_message(message.chat.id, "Thik hai! Batao kya change karna hai?")
    
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}\n\nDobara try karo ya /start se shuru karo")
        if user_id in user_sessions:
            del user_sessions[user_id]

print("Bot starting...")
bot.infinity_polling()
