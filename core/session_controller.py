# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import time
import urllib
import re
import uuid
import datetime
from logger import SessionLogger
from utils.tablet_utils import TabletSafe
import os
import json

MAX_ITEMS = 30                # e.g. max fruits expected
LIST_RECALL_ASR_GRACE_S = 90.0
LIST_RECALL_FIXED_MIN_RATIO = 0.75

LIST_RECALL_IGNORE_TOKENS = set([
    "okay", "ok", "ready", "i am ready", "i'm ready", "start", "begin",
    "go", "go ahead", "timer", "the timer has started", "time's up",
    "time is up", "fruit", "fruits", "a lot fruits", "lot fruits",
    "list fruits", "list as many fruits", "many fruits", "pepper"
])

LIST_RECALL_FRUIT_ALIASES = [
    ("black currant", "black currant"),
    ("black current", "black currant"),
    ("blackcurrant", "black currant"),
    ("passion fruit", "passion fruit"),
    ("passionfruit", "passion fruit"),
    ("dragon fruit", "dragon fruit"),
    ("star fruit", "star fruit"),
    ("kiwi fruit", "kiwi"),
    ("grape fruit", "grapefruit"),
    ("strawberries", "strawberry"),
    ("raspberries", "raspberry"),
    ("blueberries", "blueberry"),
    ("blackberries", "blackberry"),
    ("cranberries", "cranberry"),
    ("pomegranates", "pomegranate"),
    ("pineapples", "pineapple"),
    ("watermelons", "watermelon"),
    ("clementines", "clementine"),
    ("tangerines", "tangerine"),
    ("nectarines", "nectarine"),
    ("apricots", "apricot"),
    ("cherries", "cherry"),
    ("peaches", "peach"),
    ("mangoes", "mango"),
    ("oranges", "orange"),
    ("bananas", "banana"),
    ("apples", "apple"),
    ("grapes", "grape"),
    ("lemons", "lemon"),
    ("limes", "lime"),
    ("pears", "pear"),
    ("plums", "plum"),
    ("figs", "fig"),
    ("dates", "date"),
    ("melons", "melon"),
    ("coconuts", "coconut"),
    ("avocados", "avocado"),
    ("currants", "currant"),
    ("mandarins", "mandarin"),
    ("persimmons", "persimmon"),
    ("gooseberries", "gooseberry"),
    ("lychees", "lychee"),
    ("rambutans", "rambutan"),
    ("apple", "apple"),
    ("banana", "banana"),
    ("orange", "orange"),
    ("pear", "pear"),
    ("grape", "grape"),
    ("strawberry", "strawberry"),
    ("raspberry", "raspberry"),
    ("blueberry", "blueberry"),
    ("blackberry", "blackberry"),
    ("cranberry", "cranberry"),
    ("lemon", "lemon"),
    ("lime", "lime"),
    ("mango", "mango"),
    ("pineapple", "pineapple"),
    ("watermelon", "watermelon"),
    ("melon", "melon"),
    ("cantaloupe", "cantaloupe"),
    ("honeydew", "honeydew"),
    ("kiwi", "kiwi"),
    ("peach", "peach"),
    ("plum", "plum"),
    ("cherry", "cherry"),
    ("apricot", "apricot"),
    ("nectarine", "nectarine"),
    ("pomegranate", "pomegranate"),
    ("grapefruit", "grapefruit"),
    ("papaya", "papaya"),
    ("guava", "guava"),
    ("lychee", "lychee"),
    ("rambutan", "rambutan"),
    ("durian", "durian"),
    ("fig", "fig"),
    ("date", "date"),
    ("coconut", "coconut"),
    ("avocado", "avocado"),
    ("currant", "currant"),
    ("tangerine", "tangerine"),
    ("mandarin", "mandarin"),
    ("clementine", "clementine"),
    ("persimmon", "persimmon"),
    ("gooseberry", "gooseberry"),
    ("jackfruit", "jackfruit"),
    ("breadfruit", "breadfruit"),
    ("soursop", "soursop")
]

def to_unicode(x):
    """
    Python2-safe: return a unicode object.
    - if x is utf-8 bytes -> decode
    - if x is already unicode -> return
    - otherwise -> unicode(x)
    """
    try:
        unicode  # py2
    except NameError:
        # py3 fallback
        return str(x)

    if x is None:
        return u""

    if isinstance(x, unicode):
        return x

    if isinstance(x, str):
        try:
            return x.decode("utf-8")
        except Exception:
            return x.decode("utf-8", "replace")

    try:
        return unicode(x)
    except Exception:
        return unicode(str(x), "utf-8", "replace")
    
class SessionController(object):
    def __init__(self, exercises, input_queue, logger, tablet, tts, get_emotion, control_pub, session=None):
        self.total_speech_seconds = 0.0    # will accumulate
        self.adaptation_events = 0
        self.successful_adaptations = 0
        self.exercises = exercises
        self.input_queue = input_queue
        self.exercises_dict = dict(exercises)  # ← so we can look up by name later
        self.logger     = SessionLogger('session_log.csv')
        self.free_chat_mod = None   
        self.tablet = tablet
        self.tts = tts
        self.get_emotion = get_emotion
        self.control = control_pub
                # --- Tablet: fetch directly from qi.Session (simple & sync) ---
        self.session = session
        self.tablet = None
                # --------- Phase/event logging (AGX-aligned) ----------
        self.last_payload = None  # last ASR payload from AGX (contains utterance_index + epoch times)
        tablet_base_url = os.environ.get("PEPPER_TABLET_BASE_URL", "http://pepper-tablet/apps/myapp").rstrip("/")
        self.default_image_url  = os.environ.get("PEPPER_DEFAULT_IMAGE_URL", tablet_base_url + "/pepper_3.jpg")
        self.congrats_image_url = os.environ.get("PEPPER_CONGRATS_IMAGE_URL", tablet_base_url + "/hazar.jpg")
        self.session_image_url  = self.default_image_url
        # Store events next to your session_log.csv (simple + safe)
        # You can change this path later to your processed folder if you want.
        self.events_dir = os.path.join(os.getcwd(), "session_events")
        try:
            if not os.path.exists(self.events_dir):
                os.makedirs(self.events_dir)
        except Exception:
            pass

        self.events_path = None  # lazily set once we know AGX session_id
        
        if self.session:
            try:
                self.tablet = self.session.service("ALTabletService")
                # Optional: wake webview once so it’s ready
                try:
                    self.tablet.showWebview(self.default_image_url)
                except Exception:
                    pass
            except Exception:
                self.tablet = None
        elif tablet is not None:
            # keep compatibility if someone passes an existing proxy
            self.tablet = tablet
        # --- TTS wrapper: ALWAYS add body-language config ---
        #disabled
        class TTSWithBodyLanguage(object):
            def __init__(self, proxy):
                self._proxy = proxy
                self._config = {
                    "bodyLanguageMode":    "contextual",
                    "speakingMovementMode":"contextual"
                }
            def say(self, text):
                text = to_unicode(text)
                try:
                    self._proxy.say(text, self._config)
                except TypeError:
                    # some NAOqi builds don't accept config
                    self._proxy.say(text)

        # Instead of saving the raw NAOqi proxy, save the wrapper
        self.tts = TTSWithBodyLanguage(tts)
        # Pick your images

    def _ensure_events_path(self):
        if self.events_path:
            return
        if not self.last_payload:
            return
        agx_sess = self.last_payload.get("session_id")
        if not agx_sess:
            return
        fname = "events_{0}.jsonl".format(agx_sess)
        self.events_path = os.path.join(self.events_dir, fname)

    def _get_agx_anchor(self, after=False):
        agx_sess = None
        anchor_utt = None
        anchor_t = None

        if self.last_payload:
            agx_sess = self.last_payload.get("session_id")

            utt = self.last_payload.get("utterance_index", None)
            if utt is None:
                anchor_utt = 0
            else:
                try:
                    u = int(utt)
                    anchor_utt = (u + 1) if after else u
                except Exception:
                    anchor_utt = None

            # time anchor: start for start-events, end for end-events
            if after:
                anchor_t = self.last_payload.get("segment_end_epoch_s") or self.last_payload.get("segment_start_epoch_s")
            else:
                anchor_t = self.last_payload.get("segment_start_epoch_s") or self.last_payload.get("segment_end_epoch_s")

            try:
                if anchor_t is not None:
                    anchor_t = float(anchor_t)
            except Exception:
                anchor_t = None

        return agx_sess, anchor_utt, anchor_t

    def log_phase_event(self, event, exercise, phase, note=None):
        """
        Append one JSONL row marking an exercise/phase boundary.
        Uses AGX fields for alignment.
        """
        # ensure path exists
        self._ensure_events_path()
        if not self.events_path:
            return  # <-- simplest: don't write until we know agx session_id
        after = event.endswith("_end")
        agx_sess, anchor_utt, anchor_t = self._get_agx_anchor(after=after)

        row = {
            "agx_session_id": agx_sess,
            "event": event,          # e.g. "exercise_start", "phase_start", "phase_end", "exercise_end"
            "exercise": exercise,    # e.g. "free_chat", "list_recall", "moca", ...
            "phase": phase,          # e.g. "instructions", "ready_wait", "trial_1", "main", "summary"
            "anchor_utterance_index": anchor_utt,
            "anchor_epoch_s": anchor_t,

            # Pepper time included only for debugging (NOT used for alignment)
            "pepper_epoch_s": time.time(),
            "note": note
        }

        try:
            with open(self.events_path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass

    def _tablet_hide(self):
        """Hide whatever is currently displayed, safely."""
        if not self.tablet:
            return
        try:
            if hasattr(self.tablet, "hideImage"):
                self.tablet.hideImage()
        except Exception:
            pass
        try:
            if hasattr(self.tablet, "hideWebview"):
                self.tablet.hideWebview()
        except Exception:
            pass
    def _speak_with_tablet_word(self, reply, tablet_word):
        if not reply:
            return

        reply_u = (reply or "").strip()
        tw = (tablet_word or "").strip()

        # ---------- CASE 1: WordChain style (reply is just the word) ----------
        # If reply is the same as tablet_word (e.g., "giraffe"), show tablet first then speak.
        if tw and reply_u and reply_u.lower().startswith(tw.lower()):
            self.control.send_string("PAUSE")
            try:
                self._tablet_show_word(tw)
                time.sleep(0.12)   # tiny render buffer (tune 0.08–0.20)
                self.tts.say(reply_u)
            finally:
                time.sleep(0.1)
                self.control.send_string("RESUME")
            return

        # ---------- CASE 2: WordAssociation style ("Your word is: ...") ----------
        m = re.search(r"^(.*?)(\bYour word is:\s*)(.*)$", reply_u, flags=re.I | re.S)
        if not m:
            # fallback: speak normally then show
            self.control.send_string("PAUSE")
            try:
                self.tts.say(reply_u)
            finally:
                time.sleep(0.1)
                self.control.send_string("RESUME")
            if tw:
                self._tablet_show_word(tw)
            return

        before = (m.group(1) or "").strip()
        tail   = (m.group(3) or "").strip()

        parts = re.match(r"^\s*([A-Za-z][A-Za-z\-']*)(.*)$", tail, flags=re.S)
        spoken_word = parts.group(1) if parts else ""
        remainder   = (parts.group(2) if parts else tail) or ""

        self.control.send_string("PAUSE")
        try:
            if before:
                self.tts.say(before)
                time.sleep(0.08)

            self.tts.say("Your word is")  # no colon to avoid strong stop
            time.sleep(0.05)

            if tw:
                self._tablet_show_word(tw)
                time.sleep(0.12)

            tail2 = (spoken_word + " " + remainder).strip()
            if tail2:
                self.tts.say(tail2)

        finally:
            time.sleep(0.1)
            self.control.send_string("RESUME")




    def _tablet_reconnect(self):
        if not self.session:
            return False
        try:
            self.tablet = self.session.service("ALTabletService")
            return True
        except Exception:
            self.tablet = None
            return False
    def _unpack_exercise_return(self, ret):
        """
        Support both:
        - (reply, done, metrics)
        - (reply, done, metrics, tablet_text)
        """
        tablet_text = ""
        try:
            if isinstance(ret, tuple) and len(ret) == 4:
                reply, done, metrics, tablet_text = ret
            else:
                reply, done, metrics = ret
        except Exception:
            reply, done, metrics, tablet_text = ("", False, {}, "")
        return reply, done, metrics, tablet_text
    def _tablet_show_word(self, word):
        """Display a single big word on the tablet via a simple local HTML page."""
        if not word:
            return
        try:
            w = to_unicode(word)
            w_q = urllib.quote(w.encode("utf-8"))
        except Exception:
            try:
                w_q = urllib.quote(str(word))
            except Exception:
                return

        # You need to host this page in your tablet app (see HTML below)
        tablet_base_url = os.environ.get("PEPPER_TABLET_BASE_URL", "http://pepper-tablet/apps/myapp").rstrip("/")
        url = tablet_base_url + "/word.html?w=" + w_q
        self._tablet_show(url, force_reload=True)

    def _tablet_show(self, url, force_reload=False):
        if not url:
            return False

        # If proxy missing or stale, try reconnect
        if not self.tablet:
            if not self._tablet_reconnect():
                return False

        # Optional: force reload to avoid caching weirdness
        if force_reload:
            sep = "&" if "?" in url else "?"
            url = url + sep + "t=" + str(int(time.time()))

        def _do():
            lower = url.lower()
            # Prefer showImage for image URLs (much more stable than showWebview for .jpg)
            if (lower.endswith(".jpg") or lower.endswith(".jpeg") or lower.endswith(".png")) and hasattr(self.tablet, "showImage"):
                self.tablet.showImage(url)
            else:
                self.tablet.showWebview(url)
            return True

        try:
            return _do()
        except Exception as e:
            print("[Tablet] show failed:", e)

            # Reconnect + retry once (handles: Socket is not connected)
            if "Socket is not connected" in str(e) or "not connected" in str(e).lower():
                if self._tablet_reconnect():
                    try:
                        return _do()
                    except Exception as e2:
                        print("[Tablet] retry failed:", e2)



    def _clear_queue(self):
        try:
            while True:
                self.input_queue.get_nowait()
        except Exception:
            pass

    def _safe_float(self, value, default=0.0):
        try:
            return float(value)
        except Exception:
            return default

    def _is_list_recall_task_payload(self, item, module):
        mode = to_unicode(item.get("recording_mode", "")).strip().lower()
        if mode == "fixed":
            return True

        duration = self._safe_float(getattr(module, "duration", 60.0), 60.0)
        min_duration = max(5.0, duration * LIST_RECALL_FIXED_MIN_RATIO)
        segment_duration = self._safe_float(item.get("segment_duration", 0.0), 0.0)
        return segment_duration >= min_duration

    def _extract_known_fruits(self, text):
        normalized = re.sub(r"[^a-z]+", " ", to_unicode(text).lower())
        matches = []
        for alias, canonical in LIST_RECALL_FRUIT_ALIASES:
            pattern = r"\b" + re.escape(alias) + r"\b"
            for m in re.finditer(pattern, normalized):
                matches.append((m.start(), m.end(), canonical))

        matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        fruits = []
        last_end = -1
        for start, end, canonical in matches:
            if start < last_end:
                continue
            fruits.append(canonical)
            last_end = end
        return fruits

    def _extract_list_recall_items(self, text):
        known = self._extract_known_fruits(text)
        if known:
            return known

        lowered = to_unicode(text).lower()
        parts = re.split(r"[,;.\n]+|\b(?:and|then|so|next|after that)\b", lowered)
        items = []
        for part in parts:
            token = re.sub(r"[^a-z' -]+", " ", part)
            token = re.sub(r"\s+", " ", token).strip(" -'")
            if not token:
                continue
            if token in LIST_RECALL_IGNORE_TOKENS:
                continue
            if "list as many fruit" in token or "timer has started" in token:
                continue
            items.append(token)
        return items

    def _handle_special_events(self, item):
        """
        Returns True if the item was fully handled (caller should 'continue'),
        False if normal processing should continue.
        """
        try:
            ev = (item.get("event") or "").strip()
        except Exception:
            ev = ""

        if ev == "NO_HEAR":
            # Don't pause the mic here. _speak() already PAUSE/RESUME safely.
            self._speak("Sorry, I didn’t catch that. Could you please repeat?")
            # optionally clear any junk that arrived while speaking
            #self._clear_queue()
            return True

        return False


    def _wait_for_ready(self, timeout_s=120.0, heartbeat_s=0.5):
        """
        Wait until ASR hears something that matches a 'ready' pattern.
        Sends RESUME only periodically (throttled) to avoid RESUME spam.
        Returns True if matched, False on timeout.
        """
        last_resume_sent = 0.0
        ready = False
        deadline = time.time() + timeout_s

        patterns = [
            r"\bi'?m ready\b", r"\bi am ready\b",
            r"\blets?\s+go\b", r"\blet'?s\s+start\b",
            r"\bstart( please)?\b", r"\bstart now\b",
            r"\bbegin\b", r"\bokay\b", r"\bok\b",
            r"\bready\b", r"\bgo ahead\b"
        ]

        while time.time() < deadline and not ready:
            now = time.time()
            if (now - last_resume_sent) > 3.0:
                self.control.send_string("RESUME")
                last_resume_sent = now

            try:
                item = self.input_queue.get(timeout=heartbeat_s)
                self.last_payload = item

                if self._handle_special_events(item):
                    continue

                raw = item.get('text', '')
                cleaned = re.sub(r'[^a-z ]', ' ', raw.lower())

                for p in patterns:
                    if re.search(p, cleaned):
                        ready = True
                        break

            except Exception:
                pass

        return ready



                
    def _run_timer_phase(self, duration, announce=None):
        self.control.send_string("PAUSE")
        self._tablet_hide()
        time.sleep(0.1)
        if announce:
            self.tts.say(announce)
        time.sleep(duration)
        self.control.send_string("RESUME")

    def _speak(self, text):
        """Pepper speaks with expressive gestures and safe mic control."""
        if not text:
            return

        self.control.send_string("PAUSE")
        try:
            # Small, natural gesture while speaking
           # Speak text slowly (90% speed set in main)
            self.tts.say(text)

        finally:
            # Guard delay to avoid mic reactivation during Pepper’s audio tail
            time.sleep(0.1)
            self.control.send_string("RESUME")

    def _say_time_up(self, this_ex_name, next_ex_name=None):
        """Friendly, personalized 'time's up' line."""
        this_pretty = this_ex_name.replace('_', ' ')
        next_pretty = next_ex_name.replace('_', ' ') if next_ex_name else None

        if next_pretty:
            self._speak("Oh no! We’ve run out of time for {0}. Let’s keep going with {1}.".format(
                this_pretty, next_pretty))
        else:
            self._speak("Oh no! We’ve run out of time for {0}. Let’s wrap up here.".format(this_pretty))

    def run(self):
        for idx, (name, module) in enumerate(self.exercises):
            # clear spurious inputs
            exercise_id = str(uuid.uuid4())
            if idx > 0:
                self._clear_queue()
            # first exercise gets 180s, others use module.duration
            duration = 180  if idx == 0 else module.duration
                # --- handle free_chat normally ---
            if name == 'free_chat':
                self.free_chat_mod = module
                self.control.send_string("PAUSE")

                intro = module.start(self.get_emotion())
                self._tablet_show(self.default_image_url)

                if intro:
                    self.tts.say(intro)
                    self.control.send_string("RESUME")

                # ------------- FREE CHAT MAIN LOOP -------------
                start_time = time.time()
                last_item = None  # will store the last *unprocessed* ASR item, if any
                pending_ex_start = True
                while time.time() - start_time < duration:
                    try:
                        item = self.input_queue.get(timeout=0.5)
                        self.last_payload = item
                        if pending_ex_start:
                            self.log_phase_event("exercise_start", "free_chat", "start")
                            pending_ex_start = False
                    except Exception:
                        item = None

                    if not item:
                        continue
                    if self._handle_special_events(item):
                        continue
                    user_text = item.get('text', '').strip()
                    if not user_text:
                        continue

                    # normal free-chat turn
                    reply, done, metrics = module.handle_user_input(user_text)

                    # --- LOG this free_chat user turn ---
                    self.logger.log_turn(
                        session_id               = exercise_id,
                        epoch_ts                 = time.time(),
                        iso_ts                   = datetime.datetime.utcnow().isoformat(),
                        exercise                 = 'free_chat',
                        token                    = user_text,
                        response_time_s          = metrics.get('response_time'),
                        asr_confidence           = item.get('asr_confidence', ''),
                        emotion_label            = metrics.get('emotion_label', ''),
                        emotion_confidence       = metrics.get('emotion_confidence', ''),
                        task_completion_rate     = '',
                        sustained_attention_s    = '',
                        adaptation_events        = '',
                        successful_adaptations   = '',
                        extra_metric             = item.get('wav_path', ''),
                        summary_flag             = 0
                    )

                    # accumulate total speech time
                    seg_dur = item.get('segment_duration', 0.0)
                    try:
                        self.total_speech_seconds += seg_dur
                    except Exception:
                        pass

                    if reply:
                        self.control.send_string("PAUSE")
                        self.tts.say(reply)
                        self.control.send_string("RESUME")

                    if done:
                        # model says "we're finished" → exit cleanly
                        last_item = None
                        break

                    # keep track of last processed item
                    last_item = item


                # ------------- GRACEFUL ENDING -------------
                # At this point, time is up OR done=True.
                # We now check if something arrived *just after* the loop ended.
                try:
                    # Drain queue and keep only the last pending utterance, if any
                    pending_item = None
                    while True:
                        pending_item = self.input_queue.get_nowait()
                        self.last_payload = pending_item
                except Exception:
                    pending_item = None

                # ------------- GRACE WINDOW (1 minute) -------------
                # Free chat officially ends at 3 minutes, but we allow up to 60s to catch
                # the last in-flight Whisper result and reply ONCE, then move on.
                FREECHAT_GRACE_S = 60
                GRACE_SILENCE_S  = 0.8

                # optional: mark grace in events
                self.log_phase_event("phase_start", name, "grace")

                grace_deadline = time.time() + FREECHAT_GRACE_S
                pending_item = None
                last_rx = None

                # Wait for any final ASR payload to arrive (Whisper may still be running)
                while time.time() < grace_deadline:
                    try:
                        item = self.input_queue.get(timeout=0.25)  # blocks briefly
                        self.last_payload = item
                        if self._handle_special_events(item):
                            self.control.send_string("RESUME")
                            continue
                        txt = item.get('text', '').strip()
                        if not txt:
                            continue
                        pending_item = item
                        last_rx = time.time()
                    except Exception:
                        pass

                    # if we received something and then it goes quiet, finalize
                    if pending_item and last_rx and (time.time() - last_rx) >= GRACE_SILENCE_S:
                        break

                # Drain any remaining items quickly; keep only the last one
                try:
                    while True:
                        pending_item = self.input_queue.get_nowait()
                        self.last_payload = pending_item
                except Exception:
                    pass

                # If we got a pending utterance, answer it once before moving on
                if pending_item:
                    user_text = pending_item.get('text', '').strip()
                    if user_text:
                        reply, done, metrics = module.handle_user_input(user_text)

                        self.logger.log_turn(
                            session_id               = exercise_id,
                            epoch_ts                 = time.time(),
                            iso_ts                   = datetime.datetime.utcnow().isoformat(),
                            exercise                 = 'free_chat',
                            token                    = user_text,
                            response_time_s          = metrics.get('response_time'),
                            asr_confidence           = pending_item.get('asr_confidence', ''),
                            emotion_label            = metrics.get('emotion_label', ''),
                            emotion_confidence       = metrics.get('emotion_confidence', ''),
                            task_completion_rate     = '',
                            sustained_attention_s    = '',
                            adaptation_events        = '',
                            successful_adaptations   = '',
                            extra_metric             = pending_item.get('wav_path', ''),
                            summary_flag             = 0
                        )

                        if reply:
                            self.control.send_string("PAUSE")
                            self.tts.say(reply)
                            self.control.send_string("RESUME")

                # IMPORTANT: clear queue so late free_chat ASR doesn't leak into next exercise
                self._clear_queue()

                # Now, smooth transition to the main session
                self.control.send_string("PAUSE")
                greet = "Now, let's continue with the rest of our session."
                self.tts.say(greet)
                self.tts.say("We will go through some exercises to practice thinking and memory.")
                self.control.send_string("RESUME")
                time.sleep(0.2)
                self._clear_queue()
                self.log_phase_event("phase_end", name, "grace")
                continue

            if name == 'list_recall':
                # —————— wait for “ready” as before ——————
                self._run_timer_phase(30, announce="Let's take a short half-minute break. We'll continue soon.")

                self.control.send_string("PAUSE")
                intro = module.start(self.get_emotion())
                self.tts.say(intro)
                self.log_phase_event("phase_start", name, "instructions")
                self.tts.say("Say, I am ready, when you are ready to begin.")
                self.control.send_string("RESUME")
                self.log_phase_event("phase_start", name, "ready_wait")
                self._wait_for_ready()

                # —————— start the exercise ——————
                self.control.send_string("PAUSE")  # mute mic while Pepper says “The timer has started!”
                self.tts.say(module.begin_exercise())
                self.log_phase_event("phase_start", name, "task_active")
                self.log_phase_event("exercise_start", name, "start")
                self._clear_queue()
                self.control.send_string("MODE:CONTINUOUS")
                time.sleep(0.05)
                self.control.send_string("RESUME")
                start = time.time()
                list_payload = None
                list_speech_seconds = 0.0

                while time.time() - start < module.duration:
                    try:
                        item = self.input_queue.get(timeout=0.25)
                        self.last_payload = item
                        if (item.get("event") or "") == "NO_HEAR":
                            continue
                        if self._is_list_recall_task_payload(item, module):
                            list_payload = item
                            break
                    except Exception:
                        continue

                remaining = module.duration - (time.time() - start)
                if remaining > 0:
                    time.sleep(remaining)

                next_name = self.exercises[idx+1][0] if idx < len(self.exercises) - 1 else None
                this_pretty = name.replace('_', ' ')
                next_pretty = next_name.replace('_', ' ') if next_name else None
                if next_pretty:
                    time_up_msg = "Oh no! We've run out of time for {0}. Let's keep going with {1}.".format(
                        this_pretty, next_pretty)
                else:
                    time_up_msg = "Oh no! We've run out of time for {0}. Let's wrap up here.".format(this_pretty)

                self.control.send_string("PAUSE")
                self.control.send_string("MODE:FIXED")
                self.tts.say(time_up_msg)
                time.sleep(0.1)

                if list_payload is None:
                    deadline = time.time() + LIST_RECALL_ASR_GRACE_S
                    while time.time() < deadline:
                        try:
                            item = self.input_queue.get(timeout=0.5)
                            self.last_payload = item
                            if (item.get("event") or "") == "NO_HEAR":
                                continue
                            if not item.get("text", "").strip():
                                continue
                            if self._is_list_recall_task_payload(item, module):
                                list_payload = item
                                break
                        except Exception:
                            pass

                if list_payload is not None:
                    user_input = list_payload.get('text', '')
                    asr_confidence = list_payload.get('asr_confidence', 0.0)
                    segment_dur = self._safe_float(list_payload.get('segment_duration', 0.0), 0.0)
                    wav_path = list_payload.get('wav_path', '')

                    list_speech_seconds += segment_dur
                    self.total_speech_seconds += segment_dur

                    tokens = self._extract_list_recall_items(user_input)
                    for token in tokens:
                        reply, done, metrics = module.handle_user_input(token)
                        metrics['asr_confidence'] = asr_confidence

                        self.logger.log_turn(
                            session_id='',
                            epoch_ts='',
                            iso_ts='',
                            exercise=name,
                            token=token,
                            response_time_s=metrics['response_time'],
                            asr_confidence=metrics['asr_confidence'],
                            emotion_label=metrics.get('emotion_label',''),
                            emotion_confidence=metrics.get('emotion_confidence',''),
                            task_completion_rate='',
                            sustained_attention_s='',
                            adaptation_events='',
                            successful_adaptations='',
                            extra_metric=wav_path,
                            summary_flag=0
                        )

                self._clear_queue()
                # —————— log summary and speak it ——————
                 # Log and finalize
                summary, _, metrics = module.handle_user_input("")
                # after your “done” handling
                self.logger.log_turn(
                    session_id=exercise_id,
                    epoch_ts=time.time(),
                    iso_ts=datetime.datetime.utcnow().isoformat(),
                    exercise=name,
                    token='SUMMARY',
                    response_time_s=metrics['response_time'],
                    asr_confidence='',
                    emotion_label='',
                    emotion_confidence='',
                    task_completion_rate=float(len(module.user_items)) / float(MAX_ITEMS),
                    sustained_attention_s=list_speech_seconds,
                    adaptation_events=self.adaptation_events,
                    successful_adaptations=self.successful_adaptations,
                    summary_flag=1
                )


                # **Pause before Pepper speaks the summary**
                self.control.send_string("PAUSE")
                if summary:
                    self._clear_queue()
                # **Now resume for the next exercise or flow**
                self.control.send_string("RESUME")
                self.log_phase_event("phase_start", name, "summary")   # optional (nice)
                self.log_phase_event("exercise_end", name, "end")
                continue
            # --- Add break before next exercise (skip if previous was free_chat) ---
            # --- Add break before this exercise (not for free_chat or list_recall) ---
            if name not in ('free_chat', 'list_recall') and idx > 0:
                current_name = name.replace('_', ' ')
                self._run_timer_phase(
                    30,
                    announce="Let's take a short half-minute break. We'll continue soon."
                )
                self.control.send_string("PAUSE")
                self.tts.say(
                    ("Say, I am ready, when you are ready to begin {0} exercise."
                     .format(current_name))
                )
                self.control.send_string("RESUME")
                self._wait_for_ready()
                self._clear_queue()
                
            # other exercises
            self.control.send_string("PAUSE")
            intro_ret = module.start(self.get_emotion())

            intro = intro_ret
            tablet_text = ""

            if isinstance(intro, tuple) and len(intro) == 2:
                intro, tablet_text = intro
                
            if intro:
                self.tts.say(intro)
                print("introduction")
            
            # ✅ show the starting word if provided (WordChain/WordAssociation can return it)
            if tablet_text:
                self._tablet_show_word(tablet_text)
            self.control.send_string("RESUME")


            start_time = time.time()
            warned = False
            exercise_ended = False
            pending_ex_start = True  
            while True:
                elapsed = time.time() - start_time
                remaining = duration - elapsed
                if remaining <= 0:
                    next_name = self.exercises[idx+1][0] if idx < len(self.exercises) - 1 else None
                    self._say_time_up(name, next_name)
                    self.log_phase_event("exercise_end", name, "end", note="timeout")
                    exercise_ended = True
                    break



                try:
                    item = self.input_queue.get(timeout=0.2)
                except Exception:
                    item = None

                if item:
                    user_text      = item.get('text', '')
                    self.last_payload = item
                    if self._handle_special_events(item):
                        continue
                    if pending_ex_start:
                        self.log_phase_event("exercise_start", name, "start")
                        pending_ex_start = False
                    asr_confidence = item.get('asr_confidence', 0.0)
                    segment_dur    = item.get('segment_duration', 0.0)
                    ret = module.handle_user_input(user_text)
                    reply, done, metrics, tablet_text = self._unpack_exercise_return(ret)

                    # ✅ show word on tablet (only when provided)
                    if reply:
                        if tablet_text:
                            # ✅ synchronized speaking + tablet update
                            self._speak_with_tablet_word(reply, tablet_text)
                        else:
                            self.control.send_string("PAUSE")
                            self.tts.say(reply)
                            self.control.send_string("RESUME")
                    else:
                        self.control.send_string("RESUME")
                    self.logger.log_turn(
                            session_id               = exercise_id,
                            epoch_ts                 = time.time(),
                            iso_ts                   = datetime.datetime.utcnow().isoformat(),
                            exercise                 = name,                   # e.g. the current exercise ID/name
                            token                    = user_text,                   # the user’s utterance
                            response_time_s          = metrics.get('response_time'),
                            asr_confidence           = metrics.get('asr_confidence'),
                            emotion_label            = metrics.get('emotion_label'),
                            emotion_confidence       = metrics.get('emotion_confidence'),
                            task_completion_rate     = metrics.get('task_completion_rate'),
                            sustained_attention_s    = metrics.get('sustained_attention_s'),
                            adaptation_events        = metrics.get('adaptation_events'),
                            successful_adaptations   = metrics.get('successful_adaptations'),
                            summary_flag             = metrics.get('summary_flag'),
                        )
                    if done:
                        self.log_phase_event("exercise_end", name, "end", note="done")
                        exercise_ended = True
                        break

        # final wrap-up
        self.control.send_string("PAUSE")
        self.control.send_string("MODE:FIXED")
        self._tablet_hide()
        self._clear_queue()
        time.sleep(0.1)
        self._tablet_show(self.congrats_image_url, force_reload=True)
        #Do you have anything you'd like to say?
        closing = "Great work today! That completes our session."
        self.tts.say(closing)
        self.tts.say("Do you have anything you'd like to say? or say ‘goodbye’ to finish.")
        print("Session completed. Entering closing free chat.")
        self._clear_queue()
        self.control.send_string("RESUME")
        # --- Final free chat phase ---
        end_start = time.time()
        END_DURATION = 60
        explicit_end = False
        # Prefer the exact instance used earlier; fallback to dict lookup
        # ### ADD HERE (1): define a distinct name + start flag
        closing_name = "free_chat_closing"
        pending_close_start = True

        # ### ADD HERE (2) optional: mark that we prompted closing chat
        # (If you don't want this extra phase marker, you can remove this line.)
        self.log_phase_event("phase_start", closing_name, "prompt")
        free_chat_mod = self.free_chat_mod or self.exercises_dict.get('free_chat')

        while time.time() - end_start < END_DURATION:
            # Timeout reached (user didn't explicitly say goodbye) -> still end like goodbye branch

            self.control.send_string("RESUME")
            try:
                item = self.input_queue.get(timeout=0.5)
                self.last_payload = item
                if pending_close_start:
                    self.log_phase_event("phase_start", closing_name, "prompt")
                    self.log_phase_event("exercise_start", closing_name, "start")
                    pending_close_start = False
                user_text_raw = item.get('text', '').strip()
                if not user_text_raw:
                    continue

                user_text_lower = user_text_raw.lower()

                if ("goodbye" in user_text_lower or
                    "bye" in user_text_lower or
                    "exit" in user_text_lower):
                    explicit_end = True
                    self.log_phase_event("exercise_end", closing_name, "end", note="explicit_goodbye")

                    explicit_end = True 
                    self.control.send_string("PAUSE")
                    self.tts.say("Goodbye! It was nice talking with you.")
                    self._tablet_hide()
                    time.sleep(2.0)
                    self.control.send_string("RESUME")
                    break

                if free_chat_mod:
                    # This path should hit your LLM-backed module
                    reply, done, metrics = free_chat_mod.handle_user_input(user_text_raw)
                    self.log_phase_event("exercise_end", closing_name, "end", note="module_done")
                else:
                    # If we still don't have a module, don't echo; be explicit
                    reply, done, metrics = ("I'm not able to open free chat right now.", False, {"response_time": 0.0})

                if reply:
                    self.control.send_string("PAUSE")
                    self.tts.say(reply)
                    time.sleep(0.3)
                    self.control.send_string("RESUME")

                if free_chat_mod and done:
                    break

            except Exception:
                pass

            time.sleep(0.2)
        if not explicit_end:
            self.log_phase_event("exercise_end", closing_name, "end", note="timeout")
            self._clear_queue()
            self.control.send_string("PAUSE")
            self.tts.say("I enjoyed talking with you. Goodbye! It was nice talking with you.")
            self._tablet_hide()
            time.sleep(2.0)
            self.control.send_string("RESUME")
