import argparse, time, threading, subprocess, Queue as queue
import os
import zmq
from naoqi import ALBroker, ALProxy
from oaichat.oaiclient import OaiClient
from core.session_controller import SessionController
from core.logger import SessionLogger
from exercises.word_chain import WordChainExercise
from exercises.word_association import WordAssociationExercise
from exercises.list_recall import ListRecallExercise
from exercises.free_chat import FreeChatExercise
import qi
import json
# Global emotion state
current_emotion = 'neutral'
def get_emotion(): return current_emotion

# ZMQ ports
TEXT_PORT = 5556
CTRL_PORT = 5557
EMOTION_PORT = 5560

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pip', default='pepper.local')
    parser.add_argument('--pport', type=int, default=9559)
    parser.add_argument('--asr-host', default=os.environ.get('AGX_HOST', '127.0.0.1'))
    parser.add_argument('--emotion-host', default=os.environ.get('AGX_HOST', '127.0.0.1'))
    args = parser.parse_args()

    ctx = zmq.Context()
    text_sub = ctx.socket(zmq.SUB)
    text_sub.connect('tcp://%s:%d' % (args.asr_host, TEXT_PORT))
    text_sub.setsockopt(zmq.SUBSCRIBE, b'')
    input_queue = queue.Queue()

    def zmq_listener():
        global current_emotion
        while True:
            try:
                raw = text_sub.recv_string()
                data = json.loads(raw)
                input_queue.put(data)
            except Exception:
                pass

    ctrl_pub = ctx.socket(zmq.PUB)
    ctrl_pub.bind('tcp://*:%d' % CTRL_PORT)

    # Emotion subscriber
    def emotion_listener():
        global current_emotion
        sub = ctx.socket(zmq.SUB)
        sub.connect('tcp://%s:%d' % (args.emotion_host, EMOTION_PORT))
        sub.setsockopt(zmq.SUBSCRIBE, b'')
        while True:
            try:
                current_emotion = sub.recv_string()
            except Exception:
                pass

    # Start listeners
    t1 = threading.Thread(target=zmq_listener)
    t1.daemon = True
    t1.start()

    t2 = threading.Thread(target=emotion_listener)
    t2.daemon = True
    t2.start()
    # NAOqi setup
    broker = ALBroker('broker','0.0.0.0',0,args.pip,args.pport)
    tts = ALProxy('ALAnimatedSpeech', args.pip, args.pport)
    session = qi.Session()
    session.connect("tcp://" + args.pip + ":" + str(args.pport))
    # Get services
    autonomous_life = session.service("ALAutonomousLife")
    tablet = session.service("ALTabletService")
    animated_tts = session.service("ALAnimatedSpeech")   # <-- use qi service, not ALProxy
    tts = session.service("ALTextToSpeech") 
    basic_awareness = session.service("ALBasicAwareness")
    sound_detection = session.service("ALSoundDetection")
    audio_device = session.service("ALAudioDevice")   
    auto_moves      = session.service("ALAutonomousMoves") 
    # Disable bip
    try:
        autonomous_life.setState("safeguard")

        # Enable basic awareness manually
        basic_awareness = session.service("ALBasicAwareness")
        basic_awareness.setEngagementMode("FullyEngaged")
        basic_awareness.setTrackingMode("Head")   # or "Body" if you want torso motion too
        basic_awareness.startAwareness()

        # Keep head tracking smooth and stable
        people_perception = session.service("ALPeoplePerception")
        people_perception.setTimeBeforePersonDisappears(5.0)
        people_perception.setFastModeEnabled(False)
        auto_moves.setExpressiveListeningEnabled(True)
        auto_moves.setBackgroundStrategy("backToNeutral")
        # Initialize ASR and permanently disable its audio expression ("beep")
        asr = ALProxy("ALSpeechRecognition", args.pip, args.pport)
        asr.pause(True)
        asr.setAudioExpression(False)
        asr.pause(False)

        # Reinforce "no beep" when awareness refocuses on someone
        memory = session.service("ALMemory")
        tts.setParameter("speed", 85) # was 90
        tts.setVolume(0.85)
    except Exception as e:
        print("Could not disable bip sound:", e)

    
    # Prepare logger and exercises
    logger = SessionLogger()
    oai = OaiClient(user='session')
    exercises = [
        ('free_chat', FreeChatExercise(oai, ctrl_pub)),
        ('word_association', WordAssociationExercise(oai, ctrl_pub)),
        ('word_chain', WordChainExercise(oai, ctrl_pub)),
        ('list_recall', ListRecallExercise(oai, ctrl_pub)),
    ]

    # Run session
    controller = SessionController(exercises, input_queue, logger, tablet, animated_tts, get_emotion, ctrl_pub, session=session)

    controller.run()
