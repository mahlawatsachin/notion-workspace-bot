import os
import json
import requests
import telebot

# === KEYS FROM ENV VARIABLES ===
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
PARENT_PAGE_ID = os.environ['PARENT_PAGE_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

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

# === GROQ API (direct HTTP, no SDK) ===
def call_groq(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a Notion workspace architect. Always respond with valid JSON only, no markdown, no explanation."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 4000
    }
    res = requests.post(url, headers=headers, json=data, timeout=60)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]

# === WORKSPACE SPEC GENERATOR ===
def generate_workspace_spec(user_description):
    prompt = f"""Create a Notion workspace spec for: {user_description}

Return ONLY this JSON structure (no markdown):
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
- 3-6 databases, each with 4-8 properties
- Property types: title(auto), select, multi_select, date, number, rich_text, checkbox, url, email, phone_number
- Relations only between databases listed in this spec
- No extra text, just JSON"""
    
    raw = call_groq(prompt)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)

# === PROPERTY BUILDER ===
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

# === MAIN BUILD FUNCTION ===
def build_workspace(user_description):
    print(f"Building workspace for: {user_description}")
    
    print("Step 1: Generating spec with AI...")
    spec = generate_workspace_spec(user_description)
    workspace_name = spec.get("workspace_name", "My Workspace")
    
    print(f"Step 2: Creating root page: {workspace_name}")
    root_page = create_page(PARENT_PAGE_ID, workspace_name)
    root_page_id = root_page["id"]
    root_url = root_page["url"]
    
    db_name_to_id = {}
    db_urls = {}
    
    print("Step 3: Creating databases...")
    for db in spec["databases"]:
        props = build_properties_no_relations(db)
        db_res = create_database(root_page_id, db["name"], props)
        db_name_to_id[db["name"]] = db_res["id"]
        db_urls[db["name"]] = db_res["url"]
        print(f"  Created: {db['name']}")
    
    print("Step 4: Adding relations...")
    for db in spec["databases"]:
        rel_props = build_relations(db, db_name_to_id)
        if rel_props:
            try:
                update_database(db_name_to_id[db["name"]], rel_props)
            except Exception as e:
                print(f"  Skipping relations for {db['name']}: {e}")
    
    return {"root_url": root_url, "databases": db_name_to_id, "db_urls": db_urls}

# === TELEGRAM BOT ===
bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    bot.reply_to(message,
        "Steamtech Notion Workspace Builder\n\n"
        "Plain English mein describe karo apna workspace:\n\n"
        "Example:\n"
        "Build a CRM for dal mill clients with leads, orders, machines catalog and tasks\n\n"
        "Main automatically Notion mein poora workspace bana dunga!"
    )

@bot.message_handler(func=lambda m: True)
def handle(message):
    bot.reply_to(message, "Building your Notion workspace... please wait 15-30 seconds")
    try:
        result = build_workspace(message.text)
        msg = "Workspace Built Successfully!\n\n"
        msg += f"Root Page: {result['root_url']}\n\nDatabases:\n"
        for name, url in result['db_urls'].items():
            msg += f" - {name}: {url}\n"
        bot.reply_to(message, msg)
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}\n\nPlease try again.")
        print(f"Error: {e}")

print("Bot starting...")
bot.infinity_polling()
