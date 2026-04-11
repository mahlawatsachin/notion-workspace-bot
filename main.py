import os
import json
import requests
import telebot
from datetime import datetime
import re

# Config
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GROQ_API_KEY = os.environ['GROQ_API_KEY']
GOOGLE_SHEETS_WEBHOOK = os.environ.get('GOOGLE_SHEETS_WEBHOOK', '')
AUTHORIZED_USER_ID = int(os.environ.get('AUTHORIZED_USER_ID', '0'))

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ─── Groq AI ───────────────────────────────────────────────────────────────
def ask_groq(messages, system_prompt=None):
    headers = {
        'Authorization': f'Bearer {GROQ_API_KEY}',
        'Content-Type': 'application/json'
    }
    msgs = []
    if system_prompt:
        msgs.append({'role': 'system', 'content': system_prompt})
    msgs.extend(messages)
    payload = {
        'model': 'llama-3.3-70b-versatile',
        'messages': msgs,
        'temperature': 0.7,
        'max_tokens': 1024
    }
    resp = requests.post(
        'https://api.groq.com/openai/v1/chat/completions',
        headers=headers,
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']

# ─── Google Sheets (via Apps Script Web App) ───────────────────────────────
def sheets_request(action, data=None):
    if not GOOGLE_SHEETS_WEBHOOK:
        return {'status': 'error', 'message': 'Google Sheets webhook not configured'}
    payload = {'action': action}
    if data:
        payload.update(data)
    try:
        resp = requests.post(GOOGLE_SHEETS_WEBHOOK, json=payload, timeout=15)
        return resp.json()
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def add_entry(sheet_name, row_data):
    return sheets_request('add', {'sheet': sheet_name, 'data': row_data})

def get_entries(sheet_name, limit=10):
    return sheets_request('get', {'sheet': sheet_name, 'limit': limit})

def search_entries(sheet_name, query):
    return sheets_request('search', {'sheet': sheet_name, 'query': query})

def update_entry(sheet_name, row_id, updates):
    return sheets_request('update', {'sheet': sheet_name, 'id': row_id, 'data': updates})

# ─── AI Intent Parser ──────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a smart personal assistant for a pulse processing machinery business (Steamtech Innovative Machinery Pvt Ltd).
You help manage business data stored in Google Sheets with these sheets:
- Leads: track potential customers (fields: name, company, phone, status, notes)
- Orders: track machine orders (fields: customer, machine_type, quantity, value, status, date)
- Tasks: manage to-dos (fields: title, priority, due_date, status, notes)
- Contacts: store contacts (fields: name, company, phone, email, role)
- Machines: track machine inventory/enquiries (fields: model, type, capacity, price, status)

When user sends a message, respond with a JSON object (no markdown, pure JSON) with:
{
  "intent": "add|get|search|update|chat|summary",
  "sheet": "Leads|Orders|Tasks|Contacts|Machines|null",
  "data": {field: value pairs if adding/updating},
  "query": "search term if searching",
  "limit": number if getting records,
  "reply": "friendly human response to show user"
}

For general questions or chitchat, use intent=chat and provide a helpful reply.
Always be concise and business-focused."""

conversation_history = {}

def parse_intent(user_id, message):
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({'role': 'user', 'content': message})
    # Keep last 10 messages for context
    history = conversation_history[user_id][-10:]
    
    raw = ask_groq(history, SYSTEM_PROMPT)
    conversation_history[user_id].append({'role': 'assistant', 'content': raw})
    
    # Extract JSON
    try:
        # Try to find JSON in response
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            return json.loads(match.group())
    except:
        pass
    return {'intent': 'chat', 'reply': raw, 'sheet': None}

# ─── Action Executor ───────────────────────────────────────────────────────
def execute_action(parsed):
    intent = parsed.get('intent', 'chat')
    sheet = parsed.get('sheet')
    reply = parsed.get('reply', '')
    
    if intent == 'chat' or not sheet:
        return reply
    
    if intent == 'add':
        data = parsed.get('data', {})
        data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        result = add_entry(sheet, data)
        if result.get('status') == 'success':
            return f"{reply}\n\n✅ Added to {sheet} successfully!"
        else:
            return f"{reply}\n\n❌ Error: {result.get('message', 'Unknown error')}"
    
    elif intent == 'get':
        limit = parsed.get('limit', 10)
        result = get_entries(sheet, limit)
        if result.get('status') == 'success':
            entries = result.get('data', [])
            if not entries:
                return f"No entries found in {sheet}."
            text = f"📋 *{sheet}* (last {len(entries)}):\n\n"
            for i, entry in enumerate(entries, 1):
                text += f"*{i}.* " + " | ".join([f"{k}: {v}" for k, v in entry.items() if v]) + "\n"
            return text
        else:
            return f"Error fetching {sheet}: {result.get('message')}"
    
    elif intent == 'search':
        query = parsed.get('query', '')
        result = search_entries(sheet, query)
        if result.get('status') == 'success':
            entries = result.get('data', [])
            if not entries:
                return f"No results found for '{query}' in {sheet}."
            text = f"🔍 Found {len(entries)} result(s) in {sheet}:\n\n"
            for i, entry in enumerate(entries, 1):
                text += f"*{i}.* " + " | ".join([f"{k}: {v}" for k, v in entry.items() if v]) + "\n"
            return text
        else:
            return f"Error searching {sheet}: {result.get('message')}"
    
    elif intent == 'summary':
        # Get summary from all sheets
        summary_text = "📊 *Business Summary*\n\n"
        for s in ['Leads', 'Orders', 'Tasks']:
            result = get_entries(s, 5)
            if result.get('status') == 'success':
                count = len(result.get('data', []))
                summary_text += f"• {s}: {count} recent entries\n"
        return summary_text + "\n" + reply
    
    return reply

# ─── Telegram Handlers ─────────────────────────────────────────────────────
def is_authorized(user_id):
    if AUTHORIZED_USER_ID == 0:
        return True  # No restriction if not set
    return user_id == AUTHORIZED_USER_ID

@bot.message_handler(commands=['start'])
def start(message):
    if not is_authorized(message.from_user.id):
        return
    bot.reply_to(message, 
        "👋 *Steamtech AI Assistant*\n\n"
        "I'm your personal business assistant. I can help you:\n"
        "• Add leads, orders, tasks, contacts\n"
        "• Search and view your data\n"
        "• Get business summaries\n"
        "• Answer questions about your business\n\n"
        "Just talk to me naturally! Example:\n"
        "\'Add a lead: Ramesh from Delhi, interested in 2TPH dryer\'\n"
        "\'Show my latest 5 orders\'\n"
        "\'Search leads from Mumbai\'",
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['summary'])
def summary(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, 'typing')
    parsed = {'intent': 'summary', 'sheet': 'Leads', 'reply': ''}
    response = execute_action(parsed)
    bot.reply_to(message, response, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_cmd(message):
    if not is_authorized(message.from_user.id):
        return
    bot.reply_to(message,
        "*Commands:*\n"
        "/start - Welcome message\n"
        "/summary - Business overview\n"
        "/help - This help\n\n"
        "*Natural Language Examples:*\n"
        "• 'Add lead: John, ABC Corp, 9876543210, interested in dryer'\n"
        "• 'Show last 10 leads'\n"
        "• 'Search orders for Sharma'\n"
        "• 'Add task: Follow up with Delhi client, high priority, due tomorrow'\n"
        "• 'What machines do we have?'\n"
        "• 'How many orders this month?'",
        parse_mode='Markdown'
    )

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    if not is_authorized(message.from_user.id):
        return
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        parsed = parse_intent(message.from_user.id, message.text)
        response = execute_action(parsed)
        bot.reply_to(message, response, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}\nPlease try again.")

# ─── Main ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('🤖 Steamtech AI Bot starting...')
    bot.infinity_polling()
