# exercises/word_association.py
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import os
import json
import datetime
import time
import re
from exercises.base import ExerciseBase
WORD_RE = re.compile(r"[A-Za-z]+")
FILLER_WORDS = {
    "um", "um..."," umm...", "uh", "erm", "er", "hmm", "mmm", "mm", "mmh", "mmhm","uhhh",
    "ah", "ah...","oh", "a","an","the","eh", "like", "you know", "huh", "ha", "haha"
}
REPEAT_WORDS = {
    "repeat", "repeats", "pardon", "come again", "can you repeat it please",
    "say again", "i didn't hear it", "what did you say", "can you please repeat that",
    "i did not understand"
}
def _iso_utc_now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _ensure_dir(p):
    if p and not os.path.isdir(p):
        os.makedirs(p)

def _append_jsonl(path, obj):
    # Python2 unicode-safe JSONL
    line = json.dumps(obj, ensure_ascii=False)
    try:
        # py2: if unicode -> bytes
        if isinstance(line, unicode):
            line = line.encode("utf-8")
    except NameError:
        pass
    with open(path, "ab") as f:
        f.write(line + b"\n")
        f.flush()

def _wants_repeat(raw_text):
    """True if user is asking Pepper to repeat the prompt."""
    raw = (raw_text or "").strip().lower()
    # quick contains check for multi-word phrases like "say again"
    for phrase in REPEAT_WORDS:
        if " " in phrase and phrase in raw:
            return True
    # token check for single words like "repeat", "again"
    tokens = WORD_RE.findall(raw)
    return any(tok in REPEAT_WORDS for tok in tokens)
def _first_non_filler_word(raw_text):
    """
    Return first non-filler A–Z word, lowercased.
    '' if only fillers or no words.
    """
    raw = (raw_text or "").strip().lower()
    tokens = WORD_RE.findall(raw)  # "um... rapid" -> ["um","rapid"]
    for tok in tokens:
        if tok in FILLER_WORDS:
            continue
        return tok.lower()
    return ""
def _strip_farewell(raw):
    raw = (raw or "").strip()
    raw = re.sub(r"\b(bye|bye-bye|goodbye|see you|take care)\b\.?", "", raw, flags=re.I).strip()
    raw = re.sub(r"\s+", " ", raw)
    return raw
FAREWELL_RE = re.compile(r"\b(bye|bye-bye|goodbye|see you|take care)\b", re.I)

def _strip_farewell_if_appended(raw):
    raw = (raw or "").strip()
    if not raw:
        return raw
    # If it's ONLY a farewell (or basically only farewell words), keep it as-is
    words = WORD_RE.findall(raw.lower())
    if words and all(w in {"bye", "goodbye", "take", "care", "see", "you"} for w in words):
        return raw
    # Otherwise, strip farewells that are likely appended hallucinations
    return _strip_farewell(raw)

def _is_filler_only(raw_text):
    """True only if tokens exist and ALL are fillers."""
    raw = (raw_text or "").strip().lower()
    tokens = WORD_RE.findall(raw)
    if not tokens:
        return False   # silence/noise -> handle elsewhere
    for tok in tokens:
        if tok not in FILLER_WORDS:
            return False
    return True

def _has_any_word(raw_text):
    return bool(WORD_RE.findall((raw_text or "").strip()))

def _wrap(role, text):
    return {'role': role, 'content': [{'type': 'text', 'text': text}]}

class WordAssociationExercise(ExerciseBase):
    """
    Word Association Game:
    - Adjusts tone based on user emotion
    - Logs response time and emotion
    - Automatically continues until time expires
    """
    duration = 5 * 60

    def __init__(self, oai_client, control_pub,log_dir="logs", participant_id="unknown", session_id="unknown"):
        super(WordAssociationExercise, self).__init__(oai_client, control_pub)
        self.state = 'init'
        self.chat_history = []
        self.start_time = None
        self.current_emotion = 'neutral'
        self.last_user_word = None
        self.current_prompt_word = None
        self.last_thinking_tts_time = 0
        self.thinking_cooldown_s = 2.0
        self.round = 0  # association rounds (increments per accepted user word)
        self.participant_id = participant_id
        self.session_id = session_id

        _ensure_dir(log_dir)
        self.log_path = os.path.join(
            log_dir, "word_assoc_{0}_{1}.jsonl".format(participant_id, session_id)
        )


    def start(self, emotion):
        # save current emotion
        self.current_emotion = emotion
        # build system prompt
        system_prompt = (
            "You are a friendly and emotionally-aware companion robot.\n"
            "User emotion: %s\n\n"
            
            "Word Association Game instructions:\n"
            "Explain briefly: 'I'll say a word, and you tell me the first word that comes to mind.'\n"
            "Pick a fun category (e.g., animals, fruits, emotions).\n"
            "Randomly choose one word from that category.\n"
            "Do NOT ask the user to choose.\n"
            "Say: 'Your word is: [word]. What word comes to mind when you hear it?'\n"
            "Keep replies short and always follow the required output format."
        ) % emotion

        # initialize chat history and get initial word
        # initialize chat history
        self.chat_history = [_wrap('system', system_prompt)]

        # 1) Pepper-fixed explanation (always happens)
        intro = (
            "We are going to play Word Association. "
            "I will say one word, and you tell me the first word that comes to mind. "
            "Please say only one word. "
            "If you did not hear me, just say 'repeat' and I will say it again. "
        )

        # 2) Ask the model ONLY for the first word (no explanation)
        pick_prompt = (
            "Pick ONE fun category (animals, fruits, places, emotions, etc.). "
            "Choose ONE simple word from that category. "
            "Output EXACTLY 2 lines:\n"
            "CATEGORY: <category>\n"
            "WORD: <word>\n"
            "No extra text."
        )
        
        self.chat_history.append(_wrap('assistant', pick_prompt))
        picked = (self.chat.respond(self.chat_history) or "").strip()


        cat_m = re.search(r"CATEGORY:\s*([A-Za-z]+)", picked, re.I)
        word_m = re.search(r"WORD:\s*([A-Za-z]+)", picked, re.I)

        first_word = (word_m.group(1) if word_m else "elephant")
        category = (cat_m.group(1) if cat_m else "")

        self.current_prompt_word = first_word.lower()
        initial = intro + "Your word is: {0}. What word comes to mind when you hear it?".format(first_word)

        # store the spoken prompt in history (so the model has context later)
        self.chat_history.append(_wrap('assistant', initial))

        # start timing
        self.start_time = time.time()
        self.game_start_time = time.time()
        self.state = 'waiting_answer'
        self._log_event(
            exercise="Word Association",
            level=0,
            correct=None,
            response_time=None,
            emotion=self.current_emotion,
            extra={
                "event": "start",
                "prompt_word": self.current_prompt_word,
                "category": category,
                "raw_model_pick": picked
            }
        )

        return initial , self.current_prompt_word

    def handle_user_input(self, text):
        total_elapsed = time.time() - (self.game_start_time or time.time())
        tablet_text = ""

        if total_elapsed > self.duration:
            reply = "Great job! Our time for this exercise is up. We can try another one later!"
            metrics = {'response_time': 0, 'state': 'finished', 'emotion': self.current_emotion}
            return reply, True, metrics, ""

        # Repeat should NOT be logged
        if _wants_repeat(text):
            if self.current_prompt_word:
                answer = "Of course. Your word is: {0}. What word comes to mind when you hear it?".format(
                    self.current_prompt_word
                )
            else:
                self.current_prompt_word = "elephant"
                answer = "Of course. Your word is: elephant. What word comes to mind when you hear it?"
            metrics = {'response_time': 0, 'state': self.state, 'emotion': self.current_emotion, 'repeat': True}
            tablet_text = self.current_prompt_word
            return answer, False, metrics, tablet_text

        # --- Thinking / silence handling BEFORE logging ---
        # 1) Silence/no A-Z tokens: gentle nudge, but not too often
        if not _has_any_word(text):
            now = time.time()
            if now - getattr(self, "last_thinking_tts_time", 0) < self.thinking_cooldown_s:
                metrics = {'response_time': 0, 'state': self.state, 'emotion': self.current_emotion, 'silent': True}
                return "", False, metrics, self.current_prompt_word or ""
            self.last_thinking_tts_time = now
            answer = "Take your time. Just say one word when you're ready."
            metrics = {'response_time': 0, 'state': self.state, 'emotion': self.current_emotion, 'silent': True}
            return answer, False, metrics, self.current_prompt_word or ""

        # 2) Filler-only: also do not log + cooldown
        if _is_filler_only(text):
            now = time.time()
            if now - getattr(self, "last_thinking_tts_time", 0) < self.thinking_cooldown_s:
                metrics = {'response_time': 0, 'state': self.state, 'emotion': self.current_emotion, 'thinking': True}
                return "", False, metrics, self.current_prompt_word or ""
            self.last_thinking_tts_time = now
            answer = "No rush. When you're ready, say one word."
            metrics = {'response_time': 0, 'state': self.state, 'emotion': self.current_emotion, 'thinking': True}
            return answer, False, metrics, self.current_prompt_word or ""

        # From here on: user gave *some* real word content
        elapsed = time.time() - self.start_time

        if self.state == 'waiting_answer':
            cleaned = _first_non_filler_word(text)
            # If they only said "um/erm/..." (or nothing), don't advance state
            if not cleaned:
                answer = "Sorry  I did not catch that. Please say one word the first that comes to mind."
                metrics = {'response_time': elapsed, 'state': self.state, 'emotion': self.current_emotion}
                tablet_text = ""
                return answer, False, metrics, tablet_text
            self.round += 1
            self.last_user_word = cleaned
            self._log_event(
                exercise="Word Association",
                level=self.round,
                correct=None,
                response_time=elapsed,
                emotion=self.current_emotion,
                extra={
                    "state": "waiting_answer",
                    "raw_text": text,
                    "cleaned_word": cleaned,
                    "prompt_word": self.current_prompt_word
                }
            )
            # record a clean word (optional: keep raw too if you want)
            self.chat_history.append(_wrap('user', cleaned))

            prompt = "Great! Could you tell me why you chose '%s'? Then I'll give you the next word." % cleaned
            self.state = 'waiting_reason'
            self.chat_history.append(_wrap('assistant', prompt))
            answer = prompt
        elif self.state == 'waiting_reason':
            # If they only said filler, ask again (don't advance)
            if _is_filler_only(text):
                answer = "No worries could you tell me in a short sentence why you chose that word?"
                self.chat_history.append(_wrap('assistant', answer))
                metrics = {'response_time': elapsed, 'state': self.state, 'emotion': self.current_emotion}
                tablet_text = ""
                return answer, False, metrics, tablet_text

            # Keep full reason text (don’t reduce to a single word)
            clean_reason = _strip_farewell_if_appended(text)

            # if it became empty after stripping, ask again (don't advance)
            if _is_filler_only(clean_reason) or len(clean_reason.strip()) < 3:
                answer = "Sorry — could you say that again in one short sentence?"
                self.chat_history.append(_wrap('assistant', answer))
                metrics = {'response_time': elapsed, 'state': self.state, 'emotion': self.current_emotion}
                tablet_text = ""
                return answer, False, metrics, tablet_text
            self._log_event(
                exercise="Word Association (reason)",
                level=self.round,  # same round index as the association word
                correct=None,
                response_time=elapsed,
                emotion=self.current_emotion,
                extra={
                    "state": "waiting_reason",
                    "raw_text": text,
                    "reason_text": clean_reason,
                    "last_user_word": self.last_user_word,
                    "prompt_word": self.current_prompt_word
                }
            )
            self.chat_history.append(_wrap('user', clean_reason))
            prompt = (
                "You are running a Word Association Game with the user.\n"
                "The user just explained why they chose their word.\n\n"
                "Task:\n"
                "User's last word was: %s\n"
                "1) Write ONE short friendly reaction (max 12 words).\n"
                "2) Optionally add ONE simple fun fact related to the user's word/category (max 12 words).\n"
                "3) Choose a NEW category (different from the previous one if possible).\n"
                "4) Choose ONE simple word from that new category.\n"
                "Rules:\n"
                "- You (the assistant) choose the new word.\n"
                "- Do NOT ask the user to choose anything.\n"
                "- No questions except the final 'What word comes to mind...'.\n"
                "- Output EXACTLY in this format, 3 lines only:\n"
                "REACT: <reaction sentence>\n"
                "CATEGORY: <category>\n"
                "NEXT: Your word is: <WORD>. What word comes to mind when you hear it?"
            )  % (self.last_user_word or "")
            self.state = 'waiting_answer'
            self.chat_history.append(_wrap('assistant', prompt))
            reply = (self.chat.respond(self.chat_history) or "").strip()

            # basic validation + one retry
            if ("REACT:" not in reply) or ("NEXT:" not in reply):
                self.chat_history.append(_wrap(
                    'assistant',
                    "Invalid format. Output exactly 3 lines: REACT:..., CATEGORY:..., NEXT:..."
                ))
                reply = (self.chat.respond(self.chat_history) or "").strip()

            # fallback if still bad
            if ("REACT:" not in reply) or ("NEXT:" not in reply):
                react = "Nice choice."
                next_line = "Your word is: apple. What word comes to mind when you hear it?"
                self.current_prompt_word = "apple"
            else:
                react = ""
                next_line = ""
                for line in reply.splitlines():
                    line = line.strip()
                    if line.startswith("REACT:"):
                        react = line.replace("REACT:", "", 1).strip()
                    elif line.startswith("NEXT:"):
                        next_line = line.replace("NEXT:", "", 1).strip()
            m2 = re.search(r"Your word is:\s*([A-Za-z]+)", next_line)
            if m2:
                self.current_prompt_word = m2.group(1).lower()
            # final spoken text (Pepper-friendly)
            answer = (react + " " + next_line).strip()
            # store what Pepper actually said
            self.chat_history.append(_wrap('assistant', answer))
            # store the raw model output in history (optional)
            self.chat_history.append(_wrap('assistant', reply))
            tablet_text = self.current_prompt_word or ""

        else:
            # safe fallback
            prompt = "Oops, something went wrong. Let's start again. Your word is:"
            self.state = 'waiting_answer'
            self.chat_history.append(_wrap('assistant', prompt))
            reply = self.chat.respond(self.chat_history)
            self.chat_history.append(_wrap('assistant', reply))
            tablet_text = self.current_prompt_word or ""
            answer = reply

        # reset timer for next round
        self.start_time = time.time()
        # no evaluation correctness needed
        metrics = {
            'response_time': elapsed,
            'state': self.state,
            'emotion': self.current_emotion
        }
        return answer, False, metrics, tablet_text

    # ---------- unified JSONL logger ----------

    def _log_event(self, exercise, level, correct, response_time, emotion, extra=None):
        rec = {
            "iso_utc": _iso_utc_now(),
            "participant_id": getattr(self, "participant_id", "unknown"),
            "session_id": getattr(self, "session_id", "unknown"),
            "exercise": exercise,
            "round": level,
            "correct": correct,  # keep None (no correctness in this task)
            "response_time_s": float(response_time) if response_time is not None else None,
            "emotion": emotion,
            "extra": extra or {}
        }
        _append_jsonl(self.log_path, rec)
        # optional debug print
        if response_time is None:
            print("[LOG] {} | Round: {} | Emotion: {} | {}".format(exercise, level, emotion, rec["extra"].get("event", "")))
        else:
            print("[LOG] {} | Round: {} | Time: {:.2f}s | Emotion: {}".format(exercise, level, response_time, emotion))

