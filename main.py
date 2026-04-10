import os
import json
import requests
from groq import Groq
import telebot

# === KEYS FROM ENV VARIABLES ===
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
PARENT_PAGE_ID = os.environ['PARENT_PAGE_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

# === NOTION API ===
NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"

def get_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def create_page(parent_page_id, title):
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "properties": {"title": [{"type": "text", "text": {"content": title}}]},
    }
    r = requests.post(f"{BASE_URL}/pages", headers=get_headers(), data=json.dumps(payload))
    r.raise_for_status()
    return r.json()

def create_database(parent_page_id, name, properties):
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": name}}],
        "properties": properties,
    }
    r = requests.post(f"{BASE_URL}/databases", headers=get_headers(), data=json.dumps(payload))
    r.raise_for_status()
    return r.json()

def update_database(database_id, properties):
    r = requests.patch(
        f"{BASE_URL}/databases/{database_id}",
        headers=get_headers(),
        data=json.dumps({"properties": properties}),
    )
    r.raise_for_status()
    return r.json()

def format_uuid(uid):
    uid = uid.replace("-", "")
    return f"{uid[0:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}"

# === GROQ LLM PLANNER ===
SYSTEM_PROMPT = """You are an expert Notion architect for pulse processing and industrial machinery businesses.
Convert the user workspace description into a JSON object called WorkspaceSpec.
Rules:
- Return ONLY valid JSON, no markdown, no code blocks, no comments.
- Every database MUST have exactly one 'title' property named 'Name'.
- Allowed types: title, rich_text, number, select, multi_select, date, relation, people, checkbox, url, email, phone_number
- For select/multi_select: include an 'options' list.
- For relation: set 'target_db' to the exact name of the target database.
- Max 6 databases, max 20 properties each.
WorkspaceSpec format:
{"root_page": {"name": "...", "summary": "..."}, "databases": [{"name": "...", "description": "...", "properties": [{"name": "Name", "type": "title"}]}]}"""

def plan_workspace(description):
    client = Groq(api_key=GROQ_API_KEY)
    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"User description:\n{description}"}
        ],
        temperature=0.3,
    )
    text = completion.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)

def build_properties_no_relations(db_spec):
    props = {}
    for p in db_spec["properties"]:
        ptype = p["type"]
        name = p["name"]
        if ptype == "relation": continue
        if ptype == "title": props[name] = {"title": {}}
        elif ptype == "rich_text": props[name] = {"rich_text": {}}
        elif ptype == "number": props[name] = {"number": {}}
        elif ptype == "select": props[name] = {"select": {"options": [{"name": o} for o in p.get("options", [])]}}
        elif ptype == "multi_select": props[name] = {"multi_select": {"options": [{"name": o} for o in p.get("options", [])]}}
        elif ptype == "date": props[name] = {"date": {}}
        elif ptype == "people": props[name] = {"people": {}}
        elif ptype == "checkbox": props[name] = {"checkbox": {}}
        elif ptype == "url": props[name] = {"url": {}}
        elif ptype == "email": props[name] = {"email": {}}
        elif ptype == "phone_number": props[name] = {"phone_number": {}}
    return props

def build_relations(db_spec, db_name_to_id):
    rel_props = {}
    for p in db_spec["properties"]:
        if p["type"] == "relation":
            target_name = p.get("target_db")
            if not target_name: continue
            target_id = db_name_to_id.get(target_name)
            if not target_id:
                print(f"WARNING: relation target '{target_name}' not found, skipping.")
                continue
            try:
                rel_props[p["name"]] = {"relation": {"database_id": format_uuid(target_id), "type": "dual_property", "dual_property": {}}}
            except Exception as e:
                print(f"Skipping relation {p['name']}: {e}")
    return rel_props

def build_workspace(description):
    print("Step 1: Planning workspace with Groq...")
    spec = plan_workspace(description)
    print(f"Planned: {spec['root_page']['name']} with {len(spec['databases'])} databases")

    print("Step 2: Creating root page...")
    root_page = create_page(PARENT_PAGE_ID, spec["root_page"]["name"])
    root_page_id = root_page["id"]
    root_url = root_page["url"]

    db_name_to_id = {}
    db_urls = {}
    print("Step 3: Creating databases...")
    for db in spec["databases"]:
        props = build_properties_no_relations(db)
        db_res = create_database(root_page_id, db["name"], props)
        db_id = db_res["id"]
        db_url = db_res["url"]
        db_name_to_id[db["name"]] = db_id
        db_urls[db["name"]] = db_url
        print(f"  Created: {db['name']}")

    print("Step 4: Adding relations...")
    for db in spec["databases"]:
        rel_props = build_relations(db, db_name_to_id)
        if rel_props:
            try:
                update_database(db_name_to_id[db["name"]], rel_props)
                print(f"  Relations added to: {db['name']}")
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
            msg += f"  - {name}: {url}\n"
        bot.reply_to(message, msg)
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}\n\nPlease try again with a clearer description.")
        print(f"Error: {e}")

print("Bot starting...")
bot.infinity_polling()
