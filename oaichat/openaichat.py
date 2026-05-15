# -*- coding: utf-8 -*-

###########################################################
# OpenAI dialogue layer for Pepper/Nao chat.
# Adapted from ilabsweden/pepperchat's ChatGPT layer.
# This project uses the OpenAI Responses API and defaults to GPT-5 chat.
#
# Syntax:
#    python3 openaichat.py
#
# Author: Erik Billing, University of Skovde
# Created: June 2022. 
# License: MIT; see LICENSE.md.
###########################################################
import os, sys, codecs, json
from datetime import datetime
from threading import Thread
from oaichat.oairesponse import OaiResponse
import zmq
import dotenv

from openai import OpenAI
import re
import unicodedata

_LETTERS_RE = re.compile(r'[A-Za-z]+')

def letters_only_lower(s: str) -> str:
    if not s:
        return ""
    # NFKD strip accents (if you ever get café → cafe)
    s = unicodedata.normalize('NFKD', s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # keep ASCII letters only
    return "".join(ch for ch in s if 'A' <= ch <= 'Z' or 'a' <= ch <= 'z').lower()

def last_alpha_letter(s: str) -> str:
    s = letters_only_lower(s)
    return s[-1] if s else ""


dotenv.load_dotenv()

if sys.version_info[0] < 3:
    raise ImportError('OpenAI Chat requires Python 3')


class TextResp:
  def __init__(self,text):
    self.text = text
    self.json = {
      "choices": [
        {"message" :{"content": text}}
      ]
    }
  def getText(self):
    return self.text
    
class OaiChat:
  def __init__(self,user,prompt=None):
    self.log = None
    self.reset(user,prompt)
    self.client = OpenAI(api_key = os.getenv('OPENAI_KEY') or os.getenv('OPENAI_API_KEY'))
    self.model = os.getenv('OPENAI_MODEL', 'gpt-5-chat-latest')

    ctx = zmq.Context()
    self.publisher = ctx.socket(zmq.PUB)
    self.publisher.bind("tcp://*:5559")

  def reset(self,user,prompt=None):
    self.user = user
    self.history = self.loadPrompt(prompt or os.getenv('OPENAI_PROMPTFILE'))
    self.resetRequestLog()

  def resetRequestLog(self):
    # if (self.log): self.log.close()
    # logdir = os.getenv('LOGDIR')
    # if not os.path.isdir(logdir): os.mkdir(logdir)
    # log = 'requests.%s.%s.log'%(self.user,datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    # self.log = open(os.path.join(logdir,log),'a')
    # print('Logging requests to',log)
    pass

  def respond(self, inputMsgs):
      # --- Build conversation list ---
      if isinstance(inputMsgs, list):
          raw_msgs = inputMsgs
      else:
          self.history.append({'role': 'user', 'content': inputMsgs})
          raw_msgs = self.history

      # --- Normalize to Responses API parts ---
      normalized = []
      for m in raw_msgs:
          role = m.get('role')
          raw = m.get('content')

          # Map role -> part type
          part_type = "output_text" if role == "assistant" else "input_text"

          if isinstance(raw, list):
              chunks = []
              for chunk in raw:
                  raw_text = chunk.get('text') or chunk.get('content', '')
                  if isinstance(raw_text, dict):
                      raw_text = raw_text.get("text", "")
                  chunks.append({"type": part_type, "text": str(raw_text)})
              content = chunks
          else:
              content = [{"type": part_type, "text": str(raw)}]

          # Skip empty parts to avoid 400s
          content = [c for c in content if c.get("text", "").strip() != ""]
          if content:
              normalized.append({"role": role, "content": content})

      # Ensure at least a minimal system prompt (helps avoid empty first turn)
      if not any(m.get("role") == "system" for m in normalized):
          normalized.insert(0, {
              "role": "system",
              "content": [{"type": "input_text", "text": "You are a helpful assistant."}]
          })

      # --- Call GPT-5-compatible model via Responses API (streaming) ---
      partial = ""
      refusal_partial = ""
      got_any_tokens = False
      try:
          with self.client.responses.stream(
              model=self.model,
              input=normalized,
              max_output_tokens=150,
          ) as stream:
              for event in stream:
                  et = getattr(event, "type", "")

                  # Normal text tokens
                  if et == "response.output_text.delta":
                      token = event.delta
                      if token:
                          got_any_tokens = True
                          partial += token
                          # publish + print
                          try: self.publisher.send_string(token)
                          except Exception: pass
                          print(token, end="", flush=True)

                  # Refusal (safety) tokens
                  elif et == "response.refusal.delta":
                      token = event.delta
                      if token:
                          refusal_partial += token

                  # Finalization events you might want to inspect
                  elif et in (
                      "response.completed",
                      "response.error",
                      "response.output_text.done",
                      "response.refusal.done",
                  ):
                      # You can log these if debugging:
                      # print(f"\n[DEBUG] event: {et}")
                      pass

                  # Catch-all: log unexpected events to avoid silent hangs
                  else:
                      # print(f"\n[DEBUG] unexpected event: {et}")
                      pass

              # If we got a refusal but no output_text, show it
              if not got_any_tokens and refusal_partial:
                  msg = f"[refusal] {refusal_partial}"
                  print(msg, end="", flush=True)
                  partial = msg

          # If truly nothing came through, emit a placeholder so UX doesn't hang
          if not partial.strip():
              partial = "(no response)"
              print(partial, end="", flush=True)

      except Exception as e:
          partial = f"[error] {type(e).__name__}: {e}"
          print("\n" + partial, flush=True)

      # History bookkeeping
      if not isinstance(inputMsgs, list):
          self.history.append({'role': 'assistant', 'content': partial})

      return TextResp(partial)

  def loadPrompt(self,promptFile):
    promptFile = promptFile or 'openai.prompt'
    promptPath = promptFile if os.path.isfile(promptFile) else os.path.join(os.path.dirname(__file__),promptFile)
    prompt = [] # [{"role": "system", "content": "You are a helpful robot designed to output JSON."}]
    if not os.path.isfile(promptPath):
      print('WARNING: Unable to locate OpenAI prompt file',promptFile)
    else:
      with codecs.open(promptPath,encoding='utf-8') as f:
        prompt.append({'role':'system','content':f.read()})
    return prompt
    
if __name__ == '__main__':
  chat = OaiChat('User 1')

  while True:
    try:
      s = input('> ')
    except KeyboardInterrupt:
      break
    if s:
      print(chat.history)
      print(chat.respond(s).getText())
    else:
        break
  print('Closing GPT Server')
