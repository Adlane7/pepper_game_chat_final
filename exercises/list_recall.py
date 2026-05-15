# exercises/list_recall.py
import time
from exercises.base import ExerciseBase

def _wrap(role, text):
    return {'role': role, 'content': [{'type':'text','text': text}]}

class ListRecallExercise(ExerciseBase):
    duration = 1 * 60

    def __init__(self, oai_client, control_pub):
        super(ListRecallExercise, self).__init__(oai_client, control_pub)
        self.chat_history = []
        self.start_time = None
        self.user_items = []
        self.intro_given = False

    def start(self, emotion):
        # Only return an introduction string; do not send a system prompt to chat
        self.user_items = []
        self.intro_given = False
        self.start_time = None  # Timer will be started after intro
        return (
            "Next is the List Recall Exercise. "
            "In a moment, you will have one minute to list as many fruits as you can. "
            "When you are ready, I will start the timer and you can begin."
        )

    def begin_exercise(self):
        # Call this when ready to actually start the timer
        self.start_time = time.time()       
        self.intro_given = True
        return "The timer has started!"

    def handle_user_input(self, text):
        # If intro not given, ignore input
        cleaned = (text or "").strip()
        self.chat_history.append(_wrap('user', cleaned))
        if not self.intro_given:
            return None, False, {}

        elapsed = time.time() - self.start_time
        if cleaned:
            self.user_items.append(cleaned)

        done = (not cleaned and elapsed >= self.duration)
        answer = "Time's up!" if done else None

        metrics = {
            'response_time': elapsed,
            'correct': True,
            'extra_metric': cleaned,
            'items': self.user_items
        }
        return answer, done, metrics
