import os
import json
import requests
import telebot
from datetime import datetime, timedelta
import re
from collections import defaultdict

# Config
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GROQ_API_KEY = os.environ['GROQ_API_KEY']
GOOGLE_SHEETS_WEBHOOK = os.environ.get('GOOGLE_SHEETS_WEBHOOK', '')
AUTHORIZED_USER_ID = int(os.environ.get('AUTHORIZED_USER_ID', '0'))

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ─── Groq AI ─────────────────────────────────────────────────────────────────
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

# ─── Google Sheets ─────────────────────────────────────────────────────────
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

# ─── Analytics & Insights ──────────────────────────────────────────────────
def get_analytics_summary():
    """Multi-sheet analytics with AI insights"""
    summary = {}
    for sheet in ['Leads', 'Orders', 'Tasks']:
        result = get_entries(sheet, 50)
        if result.get('status') == 'success':
            data = result.get('data', [])
            summary[sheet] = {
                'count': len(data),
                'recent': data[:5] if data else []
            }
    return summary

def generate_insights(summary):
    """AI-generated business insights"""
    prompt = f"""Analyze this business data and provide 3 key insights in bullet points:

Leads: {summary.get('Leads', {}).get('count', 0)} total
Orders: {summary.get('Orders', {}).get('count', 0)} total
Tasks: {summary.get('Tasks', {}).get('count', 0)} total

Provide actionable insights in 2-3 sentences."""
    try:
        insight = ask_groq([{'role': 'user', 'content': prompt}])
        return insight
    except:
        return "Unable to generate insights at this time."

# ─── Smart Reminders ─────────────────────────────────────────────────────
reminder_store = defaultdict(list)

def parse_reminder_time(text):
    """Extract time from natural language"""
    text_lower = text.lower()
    now = datetime.now()
    
    if 'tomorrow' in text_lower:
        return now + timedelta(days=1)
    elif 'next week' in text_lower:
        return now + timedelta(weeks=1)
    elif 'hour' in text_lower:
        match = re.search(r'(\d+)\s*hour', text_lower)
        if match:
            return now + timedelta(hours=int(match.group(1)))
    elif 'minute' in text_lower:
        match = re.search(r'(\d+)\s*minute', text_lower)
        if match:
            return now + timedelta(minutes=int(match.group(1)))
    return None

def add_reminder(user_id, text, remind_time):
    reminder_store[user_id].append({
        'text': text,
        'time': remind_time,
        'created': datetime.now()
    })

# ─── AI Intent Parser ───────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Steamtech AI Assistant for a pulse processing machinery business (Steamtech Innovative Machinery Pvt Ltd).

You manage data in Google Sheets:
- Leads: name, company, phone, status, notes
- Orders: customer, machine_type, quantity, value, status, date
- Tasks: title, priority, due_date, status, notes
- Contacts: name, company, phone, email, role
- Machines: model, type, capacity, price, status

Respond with JSON (no markdown):
{
  "intent": "add|get|search|analytics|reminder|chat",
  "sheet": "Leads|Orders|Tasks|Contacts|Machines|null",
  "data": {field: value pairs},
  "query": "search term",
  "limit": number,
  "reminder_time": "extracted time if reminder intent",
  "reply": "friendly response"
}

For analytics: intent=analytics
For reminders: intent=reminder, extract reminder_time
For chat: intent=chat

Be conversational and business-focused."""

conversation_history = {}

def parse_intent(user_id, message):
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    
    conversation_history[user_id].append({'role': 'user', 'content': message})
    history = conversation_history[user_id][-10:]
    
    raw = ask_groq(history, SYSTEM_PROMPT)
    conversation_history[user_id].append({'role': 'assistant', 'content': raw})
    
    try:
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            return json.loads(match.group())
    except:
        pass
    return {'intent': 'chat', 'reply': raw, 'sheet': None}

# ─── Action Executor ────────────────────────────────────────────────────
def execute_action(parsed, user_id):
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
            return f"{reply}\n\n✅ Added to {sheet}!"
        else:
            return f"{reply}\n\n❌ Error: {result.get('message')}"
    
    elif intent == 'get':
        limit = parsed.get('limit', 10)
        result = get_entries(sheet, limit)
        if result.get('status') == 'success':
            entries = result.get('data', [])
            if not entries:
                return f"No entries in {sheet}."
            text = f"📋 *{sheet}* (last {len(entries)}):\n\n"
            for i, entry in enumerate(entries, 1):
                text += f"*{i}.* " + " | ".join([f"{k}: {v}" for k, v in entry.items() if v])[:100] + "\n"
            return text
        else:
            return f"Error: {result.get('message')}"
    
    elif intent == 'search':
        query = parsed.get('query', '')
        result = search_entries(sheet, query)
        if result.get('status') == 'success':
            entries = result.get('data', [])
            if not entries:
                return f"No results for '{query}' in {sheet}."
            text = f"🔍 Found {len(entries)} result(s):\n\n"
            for i, entry in enumerate(entries, 1):
                text += f"*{i}.* " + " | ".join([f"{k}: {v}" for k, v in entry.items() if v])[:100] + "\n"
            return text
        else:
            return f"Error: {result.get('message')}"
    
    elif intent == 'analytics':
        summary = get_analytics_summary()
        insights = generate_insights(summary)
        text = "📊 *Business Analytics*\n\n"
        for name, info in summary.items():
            text += f"• {name}: {info['count']} records\n"
        text += f"\n💡 *AI Insights:*\n{insights}"
        return text
    
    elif intent == 'reminder':
        reminder_text = parsed.get('data', {}).get('text', message)
        time_str = parsed.get('reminder_time', '')
        remind_time = parse_reminder_time(time_str or message)
        if remind_time:
            add_reminder(user_id, reminder_text, remind_time)
            return f"⏰ Reminder set for {remind_time.strftime('%d %b %Y, %I:%M %p')}\n{reminder_text}"
        else:
            return "Couldn't parse reminder time. Try: 'remind me in 2 hours' or 'remind me tomorrow'."
    
    return reply

# ─── Telegram Handlers ──────────────────────────────────────────────────
def is_authorized(user_id):
    if AUTHORIZED_USER_ID == 0:
        return True
    return user_id == AUTHORIZED_USER_ID

@bot.message_handler(commands=['start'])
def start(message):
    if not is_authorized(message.from_user.id):
        return
    bot.reply_to(message, 
        "👋 *Steamtech AI Assistant* (Pro Edition)\n\n"
        "*Features:*\n"
        "• Natural language data entry\n"
        "• Smart search & analytics\n"
        "• AI-powered business insights\n"
        "• Reminders & task tracking\n"
        "• Conversation memory\n\n"
        "*Try:*\n"
        "→ 'Add lead: Ramesh, Delhi, 9876543210'\n"
        "→ 'Show analytics'\n"
        "→ 'Remind me to call Sharma tomorrow'\n"
        "→ 'Search leads from Mumbai'",
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['analytics'])
def analytics_cmd(message):
    if not is_authorized(message.from_user.id):
        return
    bot.send_chat_action(message.chat.id, 'typing')
    summary = get_analytics_summary()
    insights = generate_insights(summary)
    text = "📊 *Business Dashboard*\n\n"
    for name, info in summary.items():
        text += f"• {name}: {info['count']} records\n"
    text += f"\n💡 *AI Insights:*\n{insights}"
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_cmd(message):
    if not is_authorized(message.from_user.id):
        return
    bot.reply_to(message,
        "*Commands:*\n"
        "/start - Introduction\n"
        "/analytics - Business dashboard\n"
        "/help - This menu\n\n"
        "*Natural Examples:*\n"
        "• 'Add lead: John, ABC Corp, 9876543210'\n"
        "• 'Show last 10 orders'\n"
        "• 'Search tasks for Delhi'\n"
        "• 'Remind me in 2 hours to follow up'\n"
        "• 'What's my business performance?'",
        parse_mode='Markdown'
    )

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    if not is_authorized(message.from_user.id):
        return
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        parsed = parse_intent(message.from_user.id, message.text)
        response = execute_action(parsed, message.from_user.id)
        bot.reply_to(message, response, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {str(e)}\nTry again or use /help")

# ─── Main ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('🤖 Steamtech AI Bot Pro starting...')
    bot.infinity_polling()
