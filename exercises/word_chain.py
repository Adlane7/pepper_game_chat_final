# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import re
import time
from exercises.base import ExerciseBase
import os
import json
import datetime

WORD_RE = re.compile(r"[A-Za-z]+")
FILLER_WORDS = {
    "um", "uh", "erm", "er", "hmm", "mmm", "mm",
    "ah", "oh", "ha", "haha"
}

REPEAT_WORDS = {
    "repeat","repeats","pardon","come again","can you repeat it please","say again",
    "i didn't hear it","what did you say","can you please repeat that","i did not understand"
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
    raw = (raw_text or "").strip().lower()
    # phrase match (e.g., "come again")
    for phrase in REPEAT_WORDS:
        if " " in phrase and phrase in raw:
            return True
    # token match
    tokens = WORD_RE.findall(raw)
    return any(tok in REPEAT_WORDS for tok in tokens)

def _wrap(role, text):
    # Py2 unicode safety
    try:
        basestring  # noqa
    except NameError:
        basestring = (str,)

    if not isinstance(role, basestring):
        role = unicode(role)
    if not isinstance(text, basestring):
        text = unicode(text)
    return {'role': role, 'content': [{'type': 'text', 'text': text}]}

def _clean_word(s):
    """First contiguous A–Z word, lowercased ('' if none)."""
    if not s:
        return ""
    m = WORD_RE.search(s)
    return m.group(0).lower() if m else ""

def _normalize_single_letter(s):
    """Map tee/tea/letter t → 't'."""
    t = (s or "").strip().lower()
    variants = {
        "t": {"t", "tee", "tea", "letter t"},
        "a": {"a", "ay", "eh", "letter a"},
        "b": {"b", "bee", "be", "letter b"},
        "c": {"c", "see", "sea", "letter c"},
        "d": {"d", "dee", "letter d"},
    }
    for letter, forms in variants.items():
        if t in forms:
            return letter
    if t.startswith("letter ") and len(t) == len("letter x") and t[-1].isalpha():
        return t[-1]
    return t
def _is_only_filler(raw_text):
    raw = (raw_text or "").strip().lower()
    tokens = WORD_RE.findall(raw)
    if not tokens:
        return False  # treat as empty/noise (handle separately)
    for tok in tokens:
        if tok not in FILLER_WORDS:
            return False
    return True
class WordChainExercise(ExerciseBase):
    duration = 5 * 60

    def __init__(self, oai_client, control_pub, log_dir="logs", participant_id="unknown", session_id="unknown"):
        super(WordChainExercise, self).__init__(oai_client, control_pub)
        self.participant_id = participant_id
        self.session_id = session_id

        _ensure_dir(log_dir)
        self.log_path = os.path.join(log_dir, "word_chain_{0}_{1}.jsonl".format(participant_id, session_id))

        self.round = 0
        self.chat_history = []
        self.start_time = None
        self.last = None
        self.used = []
        self.used_set = set()
        self.current_emotion = 'neutral'
        self.game_start_time = None
        self.fail_streak = 0
        self.max_fail_streak = 3
        self.easy_seeds = ["music", "house", "water", "tree", "sun", "happy", "table"]
        self.seed_idx = 0
        self.last_prompt_tts = None
        # ---------- lifecycle ----------
    def _say_letter(self, ch):
        """Return a TTS-friendly name for a single letter."""
        if not ch:
            return "that letter"
        ch = ch.lower()
        names = {
            "a": "aye",
            "b": "bee",
            "c": "see",
            "d": "dee",
            "e": "e",
            "f": "f",
            "g": "g",
            "h": "aitch",
            "i": "eye",
            "j": "jay",
            "k": "kay",
            "l": "el",
            "m": "em",
            "n": "n",
            "o": "oh",
            "p": "pee",
            "q": "cue",
            "r": "are",
            "s": "s",
            "t": "tee",
            "u": "you",
            "v": "vee",
            "w": "double you",
            "x": "ex",
            "y": "why",
            "z": "zed",
        }
        return names.get(ch, "the letter " + ch)

    def start(self, emotion, starting_word="garden"):
        self.fail_streak = 0
        self.current_emotion = emotion
        self.game_start_time = time.time()
        self.start_time = self.game_start_time
        system_prompt = (
            "You are a friendly and emotionally-aware companion robot.\n"
            "You are now in Word Chain Game mode.\n"
            "User emotion: {emo}\n"
            "Guide the game.\n"
            "Rules:\n"
            "- You choose the next word on your turns.\n"
            "- Do not ask the user to choose for you.\n"
        ).format(emo=emotion)
        self.chat_history = [_wrap('system', system_prompt)]

        # clean seed
        seed = _clean_word(starting_word) or "tree"
        self.last = seed
        self.used = [seed]
        self.used_set = {seed}
        self.start_time = time.time()

        # 1️⃣ Display/LLM version (formatted)
        opening_text = (
            "Let’s play the word chain game! "
            "Each player says a word that starts with the last letter of the previous word.\n\n"
            "Please say only one word each turn. If you didn’t hear me, say **repeat**.\n\n"
            "Starting word: **{w}** \n"
            "Your turn! What word starts with **{letter}**?"
        ).format(w=seed, letter=self._expected_letter())

        # 2️⃣ Pepper TTS version (plain speech-friendly)
        tts_text = (
            "Let's play the word chain game. "
            "Each player says a word that starts with the last letter of the previous word. "
            "If you did not hear me, just say 'repeat' and I will say it again. "
            "The starting word is {w}. "
            "Your turn! What word starts with {letter}?"
        ).format(w=seed, letter=self._expected_letter())

        # Log the formatted version
        self.chat_history.append(_wrap('assistant', opening_text))

        # Return the TTS version for Pepper to speak
        self.last_prompt_tts = tts_text
        return tts_text, seed

    # ---------- core helpers ----------

    def _expected_letter(self):
        """Return the last alphabetical character of the current word, or None."""
        if not self.last:
            return None
        m = re.search(r"[A-Za-z](?=[^A-Za-z]*$)", self.last)  # last A–Z in the string
        return m.group(0).lower() if m else None
    def _normalize_user_word(self, raw_text):
        """
        Normalize ASR text → single clean word for the game.

        Behaviour:
        - If the user clearly says a single letter (e.g. "letter t"), map to 't'.
        - Otherwise, scan tokens and return the first non-filler word.
        - If only fillers (um, uh, ha ha...), return '' so we treat it as "no word".
        """
        raw = (raw_text or "").strip().lower()

        # 1) Single-letter shortcut (keep your existing behaviour)
        single = _normalize_single_letter(raw)
        if len(single) == 1 and single.isalpha():
            return single

        # 2) Token-based: skip fillers, keep first real word
        tokens = WORD_RE.findall(raw)  # e.g. "Um... rapid." -> ["um", "rapid"]
        for tok in tokens:
            if tok in FILLER_WORDS:
                continue
            # first non-filler token is our candidate word
            return tok.lower()

        # 3) Only fillers or no tokens at all
        return ""


    def _check_correct(self, cleaned_word):
        """Return (correct, reason) where reason in {None,'empty','start','repeat'}."""
        if not cleaned_word:
            return False, 'empty'
        exp = self._expected_letter()
        if exp and not cleaned_word.startswith(exp):
            return False, 'start'
        if cleaned_word in self.used_set:
            return False, 'repeat'
        return True, None

    def _choose_bot_word(self, starts_with):
        prompt = (
            "Respond with EXACTLY ONE English word that starts with '{0}' "
            "that has not been used in this game. No punctuation, no extra text."
        ).format(starts_with)

        temp_history = list(self.chat_history) + [_wrap('system', prompt)]
        raw = (self.chat.respond(temp_history) or "").strip()
        return _clean_word(raw)
    
    def _next_easy_seed(self):
        seed = self.easy_seeds[self.seed_idx % len(self.easy_seeds)]
        self.seed_idx += 1
        return seed

    # ---------- main step ----------

    def handle_user_input(self, text):
        # Repeat request: do NOT count as attempt, do NOT log
        tablet_text = ""
        if _wants_repeat(text):
            exp = self._expected_letter() or ''
            if self.last_prompt_tts:
                answer = self.last_prompt_tts
            else:
                # fallback if not set for some reason
                answer = "No problem. Say a word starting with '{0}'.".format(exp)

            metrics = {
                'response_time': 0,
                'correct': None,
                'round': self.round,
                'emotion': self.current_emotion,
                'repeat': True
            }
            tablet_text = self.last or ""
            return answer, False, metrics , tablet_text
        # 0) Filler-only thinking out loud: don't punish it
        if _is_only_filler(text):
            # Option A: say something gentle (recommended)
            exp = self._expected_letter() or ''
            answer = "Take your time. When you're ready, say one word starting with {0}.".format(self._say_letter(exp))
            metrics = {
                'response_time': 0,
                'correct': None,
                'round': self.round,
                'emotion': self.current_emotion,
                'thinking': True
            }
            # Do NOT increase fail_streak, do NOT log incorrect
            return answer, False, metrics, self.last or ""        
        elapsed = time.time() - (self.start_time or time.time())
        total_elapsed = time.time() - self.game_start_time
        if total_elapsed > self.duration:
            reply = "Great job! Our time for this exercise is up. We can try another one later!"
            metrics = {'response_time': 0, 'state': 'finished', 'emotion': self.current_emotion}
            tablet_text = ""
            return reply, True, metrics , tablet_text # <-- Return True to signal "done"
        cleaned = self._normalize_user_word(text)
        correct, reason = self._check_correct(cleaned)

        # log
        self.log_performance(
            exercise='Word Chain',
            level=self.round + 1,
            correct=correct,
            response_time=elapsed,
            extra_metric={
                "reason": reason,                # empty/start/repeat/None
                "expected_letter": self._expected_letter(),
                "raw_text": text,
                "cleaned_word": cleaned,
                "last_word": self.last,
                "fail_streak": self.fail_streak
            },
            emotion=self.current_emotion
        )

        # keep transcript
        self.chat_history.append(_wrap('user', text))

        if correct:
            # accept user word
            self.fail_streak = 0
            self.round += 1
            self.used.append(cleaned)
            self.used_set.add(cleaned)
            self.last = cleaned

            # bot's turn
            next_letter = self._expected_letter()
            if not next_letter:
                next_letter = "a"
            bot_word = self._choose_bot_word(next_letter)

            # validate bot word; if invalid, fall back with a neutral prompt
            if bot_word and (bot_word not in self.used_set) and bot_word.startswith(next_letter):
                self.chat_history.append(_wrap('assistant', bot_word))
                self.last = bot_word
                self.used.append(bot_word)
                self.used_set.add(bot_word)
                answer = bot_word
                tablet_text = bot_word   
                self.last_prompt_tts = answer
            else:
                answer = "Nice one! Your turn again — pick a word starting with '{0}'.".format(next_letter)
                self.last_prompt_tts = answer
                self.chat_history.append(_wrap('assistant', answer))
        else:
            exp = self._expected_letter() or ''
            self.fail_streak += 1

            if reason == 'empty':
                answer = "Sorry, I did not hear that word. Could you please say one word starting with '{0}'.".format(exp)
            elif reason == 'start':
                letter_say = self._say_letter(exp)
                answer = "Sorry, I did not hear that word. Could you please say one word starting with {} . ".format(letter_say)
            else:  # repeat
                answer = "We already used that one. Please choose a new word starting with '{0}'.".format(exp)

            # --- anti-deadlock: after 3 failed attempts, restart or hint ---
            if self.fail_streak >= self.max_fail_streak:
                seed = self._next_easy_seed()   # rotate seeds each restart
                self.last = seed
                self.used = [seed]
                self.used_set = {seed}
                exp = self._expected_letter() or ''
                answer = (
                    "No worries. Let's try an easier word. "
                    "Starting word is {w}. Your turn — say a word starting with '{l}'."
                ).format(w=seed, l=exp)
                self.fail_streak = 0
                tablet_text = seed
            else:
                tablet_text = ""
            self.last_prompt_tts = answer
            self.chat_history.append(_wrap('assistant', answer))
            
        self.start_time = time.time()
        total_elapsed = time.time() - (self.game_start_time or time.time())
        done = (total_elapsed >= self.duration)
        metrics = {
            'response_time': elapsed,
            'correct': correct,
            'round': self.round,
            'emotion': self.current_emotion
        }
        return answer, done, metrics, tablet_text

    # ---------- logging ----------

    def log_performance(self, exercise, level, correct, response_time, extra_metric, emotion):
        rec = {
            "iso_utc": _iso_utc_now(),
            "participant_id": getattr(self, "participant_id", "unknown"),
            "session_id": getattr(self, "session_id", "unknown"),
            "exercise": exercise,
            "round": level,
            "correct": correct,                 # True/False/None
            "response_time_s": float(response_time) if response_time is not None else None,
            "emotion": emotion,
            "extra": extra_metric or {}
        }
        _append_jsonl(self.log_path, rec)

        # keep print for debugging (optional)
        print("[LOG] {0} | Round: {1} | Correct: {2} | Time: {3:.2f}s | Emotion: {4}".format(
            exercise, level, correct, response_time, emotion
        ))