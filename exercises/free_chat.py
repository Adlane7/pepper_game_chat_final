# exercises/free_chat.py
# -*- coding: utf-8 -*-
from exercises.base import ExerciseBase
import time, re

def _wrap(role, text):
    return {'role': role, 'content': [{'type': 'text', 'text': text}]}

# simple, robust-ish extractor (first name only)
NAME_PATTERNS = [
    r"\bmy name is\s+([A-Z][a-z]+)\b",
    r"\bi am\s+([A-Z][a-z]+)\b",
    r"\bi'm\s+([A-Z][a-z]+)\b",
    r"\bit'?s\s+([A-Z][a-z]+)\b",
]

def extract_first_name(text):
    t = text.strip()
    for pat in NAME_PATTERNS:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            name = m.group(1)
            # normalize to Title Case, avoid very short/weird tokens
            if 2 <= len(name) <= 20:
                return name.title()
    return None

class FreeChatExercise(ExerciseBase):
    duration = 5 * 60  # seconds

    def __init__(self, oai_client, control_pub):
        super(FreeChatExercise, self).__init__(oai_client, control_pub)
        self.chat_history = []
        self.initial = True

    def start(self, emotion):
        overview = (
            "You are Pepper robot.\n"
            "You will engage in an interaction with the user for 5 minutes.\n"
        )
        # --- Improved introduction prompt ---
        prompt = overview + (
            "Start the chat with a warm and natural greeting, for example:\n"
            "'Hello! It’s lovely to see you today.' or 'Hi there! How are you feeling right now?'\n"
            "Keep the tone positive, curious, and supportive.\n"
            "You can talk about simple daily topics — how their day is going, the weather, hobbies, or anything relaxing.\n"
            "Avoid asking for their name. Focus on building comfort and connection.\n"
            "Keep replies short, conversational, and varied — avoid repeating phrases or sounding robotic."
        )

        #prompt = overview + (
        #    "To start, introduce yourself, ask how they are feeling "
        #    "if you don't know it yet.\n"
        #    "If you learn a name, ACKNOWLEDGE IT ONCE only (e.g., 'Nice to meet you, <Name>'). "
        #    "DO NOT repeat the user's name in every reply. Use it sparingly later."
        #    "Vary the topic. Keep responses concise and conversational"
        #)

        self.chat_history = [_wrap('system', prompt)]
        resp = self.chat.respond(self.chat_history)
        self.chat_history.append(_wrap('assistant', resp))
        return resp

    def handle_user_input(self, text):
        t0 = time.time()
        self.chat_history.append(_wrap('user', text))

        # Detect name (if user volunteers it)
        detected_name = extract_first_name(text)

        reply = self.chat.respond(self.chat_history)
        self.chat_history.append(_wrap('assistant', reply))

        elapsed = time.time() - t0
        metrics = {
            'response_time': elapsed,
            'correct': True,
            'round': None,
            'emotion': None,
        }
        if detected_name:
            metrics['detected_name'] = detected_name

        # keep chatting until controller times out
        return reply, False, metrics