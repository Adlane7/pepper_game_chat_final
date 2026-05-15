# -*- coding: utf-8 -*-
from oaichat.oaiserver import OaiServer
from optparse import OptionParser
import sys, time, os, uuid, json, datetime
from pathlib import Path

parser = OptionParser()
parser.add_option("--prompt", dest="prompt",
    help="Path to prompt file.", default='pepper')
parser.add_option("--no-repl", action="store_true", dest="no_repl",
    help="Run without interactive console.", default=False)

def make_transcript_path():
    # Shared env from bash
    participant_id = os.environ.get("PARTICIPANT_ID", "participant_unknown")
    session_start = float(os.environ.get("SESSION_START_EPOCH_S", time.time()))
    session_id = uuid.uuid4().hex

    # Same date convention as mic
    session_date = datetime.datetime.utcfromtimestamp(session_start).strftime("%Y-%m-%d")

    base_dir = Path("recordings") / session_date / participant_id / "chat"
    sess_dir = base_dir / ("sess-" + session_id)
    sess_dir.mkdir(parents=True, exist_ok=True)

    # store a "latest_chat_session.txt" shortcut if useful
    with open(Path("recordings") / "latest_chat_session.txt", "w") as f:
        f.write(str(sess_dir))

    return sess_dir / "transcript.jsonl", participant_id, session_id, session_start

def safe_get_text(reply):
    return reply.getText() if hasattr(reply, "getText") else reply

def log_turn(path, role, text, meta=None):
    if not text:
        return
    if meta is None:
        meta = {}
    payload = {
        "epoch_ts": time.time(),
        "iso_ts": datetime.datetime.utcnow().isoformat(),
        "role": role,       # "user" or "assistant" or "system"
        "text": text,
    }
    payload.update(meta)
    with open(path, "a") as f:
        f.write(json.dumps(payload) + "\n")

if __name__ == '__main__':
    (opts, args_) = parser.parse_args()
    transcript_path, participant_id, session_id, session_start = make_transcript_path()

    # You can inject some initial meta line
    log_turn(transcript_path, "meta",
             "Session started",
             {"participant_id": participant_id,
              "session_id": session_id,
              "session_start_epoch_s": session_start,
              "prompt_file": opts.prompt + ".prompt"})

    server = OaiServer(user='User 1', prompt=opts.prompt + '.prompt')
    server.start()

        # -------------------- NEW: wrap respond() --------------------
    try:
        _orig_respond = server.respond

        def logged_respond(text, *args, **kwargs):
            # log the incoming user text
            log_turn(transcript_path, "user", text, {"source": "respond"})
            reply = _orig_respond(text, *args, **kwargs)
            out = safe_get_text(reply)
            log_turn(transcript_path, "assistant", out, {"source": "respond"})
            return reply

        server.respond = logged_respond
        log_turn(transcript_path, "system", "Logging hook installed on server.respond()", {})
    except Exception as e:
        print("Could not wrap server.respond():", e)
        log_turn(transcript_path, "system", "Could not wrap server.respond()", {"error": str(e)})

    # -------------------- NEW: history tail fallback --------------------
    last_hist_len = 0

    def tail_history_once():
        """Log any new history entries since last check."""
        global last_hist_len
        try:
            hist = getattr(server, "history", [])
            if hist is None:
                return
            if len(hist) <= last_hist_len:
                return
            new_items = hist[last_hist_len:]
            for h in new_items:
                role = h.get("role", "unknown")
                content = h.get("content", "")
                log_turn(transcript_path, role, content, {"source": "history"})
            last_hist_len = len(hist)
        except Exception as e:
            # don't crash the server if logging fails
            print("tail_history_once error:", e)
    try:
        # If no TTY (nohup) or user requested no REPL → just keep server alive
        if opts.no_repl or not sys.stdin.isatty():
            while True:
                time.sleep(1)
        else:
            while True:
                s = input('> ')
                if s == 'exit':
                    break
                elif s == 'history':
                    for line in server.history:
                        print(line)
                elif s == 'reset':
                    server.reset(server.user)
                    print('Dialogue history reset.')
                    log_turn(transcript_path, "system",
                             "History reset by operator.", {})
                elif s == 'start interview':
                    server.reset(server.user)
                    server.history.append({
                        'role': 'system',
                        'content': 'How would you start the conversation?'
                    })
                    # log that system turn
                    log_turn(transcript_path, "system",
                             "How would you start the conversation?",
                             {"tag": "start_interview"})
                    reply = server.respond(s)
                    out = reply.getText() if hasattr(reply, 'getText') else reply
                    print(out)
                    log_turn(transcript_path, "assistant", out)
                elif s:
                    log_turn(transcript_path, "user", s)
                    reply = server.respond(s)
                    out = reply.getText() if hasattr(reply, 'getText') else reply
                    print(out)
                    log_turn(transcript_path, "assistant", out)
    except KeyboardInterrupt:
        pass
    finally:
        # Optional: dump full history as a single JSON too
        try:
            hist_path = transcript_path.with_name("history_full.json")
            with open(hist_path, "w") as f:
                json.dump(server.history, f, indent=2)
        except Exception as e:
            print("Could not dump history_full.json:", e)

        server.stop()
    print('GPT Server closed.')

