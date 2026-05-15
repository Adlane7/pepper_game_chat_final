import time

class ExerciseBase(object):
    duration = 0

    def __init__(self, oai_client, control_pub):
        self.chat = oai_client
        self.control = control_pub

    def start(self, emotion):
        raise NotImplementedError

    def handle_user_input(self, text):
        raise NotImplementedError

    def render_timer(self, remaining):
        with open('templates/timer.html') as f:
            return f.read().replace('{{remaining}}', str(remaining))