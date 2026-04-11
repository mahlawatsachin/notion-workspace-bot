import os
import json
import requests
import telebot
from datetime import datetime, timedelta
import re

# Config
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
PARENT_PAGE_ID = os.environ['PARENT_PAGE_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

# Session storage
user_db_ids = {}  # Store database IDs per user

# Notion API helpers
def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
    }

def search_notion(query=""):
    data = {"query": query} if query else {}
    res = requests.post(f"{NOTION_BASE}/search", headers=notion_headers(), json=data)
    return res.json()

def create_database(parent_id, title, properties):
    data = {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties
    }
    res = requests.post(f"{NOTION_BASE}/databases", headers=notion_headers(), json=data)
    return res.json()

def create_page(database_id, properties):
    data = {
        "parent": {"database_id": database_id},
        "properties": properties
    }
    res = requests.post(f"{NOTION_BASE}/pages", headers=notion_headers(), json=data)
    return res.json()

def query_database(database_id, filter_obj=None):
    data = {}
    if filter_obj:
        data["filter"] = filter_obj
    res = requests.post(f"{NOTION_BASE}/databases/{database_id}/query", headers=notion_headers(), json=data)
    return res.json()

def update_page(page_id, properties):
    data = {"properties": properties}
    res = requests.patch(f"{NOTION_BASE}/pages/{page_id}", headers=notion_headers(), json=data)
    return res.json()

def archive_page(page_id):
    data = {"archived": True}
    res = requests.patch(f"{NOTION_BASE}/pages/{page_id}", headers=notion_headers(), json=data)
    return res.json()

# Date parsing
def parse_date(text):
    text = text.lower().strip()
    today = datetime.now()
    
    if text in ["aaj", "today", "आज"]:
        return today.strftime("%Y-%m-%d")
    elif text in ["kal", "tomorrow", "कल"]:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif text in ["parso", "परसों"]:
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    elif "week" in text or "hafte" in text:
        return (today + timedelta(days=7)).strftime("%Y-%m-%d")
    return None

# AI call
def call_groq(prompt, system_msg=None):
    url = "https://api.groq.com/openai/v1/chat/completions"
    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": prompt})
    
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1000
    }
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    res = requests.post(url, headers=headers, json=data, timeout=30)
    return res.json()["choices"][0]["message"]["content"]

# Setup databases
def setup_workspace(user_id):
    # Create Tasks DB
    tasks_props = {
        "Name": {"title": {}},
        "Status": {"select": {"options": [
            {"name": "Todo", "color": "gray"},
            {"name": "In Progress", "color": "blue"},
            {"name": "Done", "color": "green"}
        ]}},
        "Priority": {"select": {"options": [
            {"name": "High", "color": "red"},
            {"name": "Medium", "color": "yellow"},
            {"name": "Low", "color": "gray"}
        ]}},
        "Due Date": {"date": {}},
        "Notes": {"rich_text": {}}
    }
    
    tasks_db = create_database(PARENT_PAGE_ID, "📋 Tasks", tasks_props)
    
    # Create Meetings DB
    meetings_props = {
        "Name": {"title": {}},
        "Date": {"date": {}},
        "Attendees": {"rich_text": {}},
        "Notes": {"rich_text": {}},
        "Status": {"select": {"options": [
            {"name": "Scheduled", "color": "blue"},
            {"name": "Completed", "color": "green"},
            {"name": "Cancelled", "color": "red"}
        ]}}
    }
    
    meetings_db = create_database(PARENT_PAGE_ID, "📅 Meetings", meetings_props)
    
    # Create Notes DB
    notes_props = {
        "Name": {"title": {}},
        "Content": {"rich_text": {}},
        "Category": {"select": {"options": [
            {"name": "Work", "color": "blue"},
            {"name": "Personal", "color": "green"},
            {"name": "Ideas", "color": "purple"}
        ]}},
        "Created": {"date": {}}
    }
    
    notes_db = create_database(PARENT_PAGE_ID, "📝 Notes", notes_props)
    
    # Store DB IDs
    user_db_ids[user_id] = {
        "tasks": tasks_db["id"],
        "meetings": meetings_db["id"],
        "notes": notes_db["id"]
    }
    
    return user_db_ids[user_id]

# Clean workspace
def clean_workspace():
    results = search_notion()
    deleted = 0
    
    for item in results.get('results', []):
        parent = item.get('parent', {})
        if parent.get('type') == 'page_id' and parent.get('page_id') == PARENT_PAGE_ID:
            archive_page(item['id'])
            deleted += 1
    
    return deleted

# Bot
bot = telebot.TeleBot(TELEGRAM_TOKEN)

@bot.message_handler(commands=['start', 'help'])
def welcome(message):
    bot.reply_to(message,
        "🚀 *Notion Personal Assistant*\n\n"
        "Commands:\n"
        "/setup - Setup databases (first time)\n"
        "/clean - Clean workspace\n\n"
        "💬 Just chat naturally:\n"
        "• Add task: Call vendor tomorrow\n"
        "• Show my tasks\n"
        "• Add meeting: Client call Monday 3pm\n"
        "• Add note: Project ideas",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['setup'])
def setup_cmd(message):
    bot.reply_to(message, "⚙️ Setting up your Notion workspace...")
    try:
        user_id = message.from_user.id
        dbs = setup_workspace(user_id)
        bot.send_message(message.chat.id,
            "✅ *Workspace Ready!*\n\n"
            "Databases created:\n"
            "📋 Tasks\n📅 Meetings\n📝 Notes\n\n"
            "Now you can start chatting!",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['clean'])
def clean_cmd(message):
    bot.reply_to(message, "🧹 Cleaning workspace...")
    try:
        count = clean_workspace()
        bot.send_message(message.chat.id, f"✅ Deleted {count} items. Workspace is clean!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.lower()
    
    # Check if setup done
    if user_id not in user_db_ids:
        bot.reply_to(message, "⚠️ Please run /setup first!")
        return
    
    try:
        # Add task
        if "add task" in text or "task:" in text:
            task_text = text.split(":")[1].strip() if ":" in text else text.replace("add task", "").strip()
            
            # Extract date
            due_date = None
            for word in task_text.split():
                date = parse_date(word)
                if date:
                    due_date = date
                    break
            
            props = {
                "Name": {"title": [{"text": {"content": task_text}}]},
                "Status": {"select": {"name": "Todo"}}
            }
            
            if due_date:
                props["Due Date"] = {"date": {"start": due_date}}
            
            create_page(user_db_ids[user_id]["tasks"], props)
            bot.reply_to(message, f"✅ Task added: {task_text}")
        
        # Show tasks
        elif "show" in text and "task" in text:
            results = query_database(user_db_ids[user_id]["tasks"])
            if not results.get("results"):
                bot.reply_to(message, "No tasks found!")
                return
            
            msg = "📋 *Your Tasks:*\n\n"
            for page in results["results"][:10]:
                title = page["properties"]["Name"]["title"][0]["plain_text"] if page["properties"]["Name"]["title"] else "Untitled"
                status = page["properties"]["Status"]["select"]["name"] if page["properties"]["Status"]["select"] else "No status"
                msg += f"• {title} - {status}\n"
            
            bot.reply_to(message, msg, parse_mode="Markdown")
        
        # Add note
        elif "add note" in text or "note:" in text:
            note_text = text.split(":")[1].strip() if ":" in text else text.replace("add note", "").strip()
            
            props = {
                "Name": {"title": [{"text": {"content": note_text[:100]}}]},
                "Content": {"rich_text": [{"text": {"content": note_text}}]},
                "Created": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}}
            }
            
            create_page(user_db_ids[user_id]["notes"], props)
            bot.reply_to(message, f"✅ Note saved!")
        
        else:
            bot.reply_to(message, "I can help with:\n• Add task/note\n• Show tasks\n• Add meetings")
    
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

print("Bot starting...")
bot.infinity_polling()
