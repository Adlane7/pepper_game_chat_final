# logger.py
# -*- coding: utf-8 -*-
import csv, threading, time, datetime, os, uuid, sys

PY2 = (sys.version_info[0] < 3)

class SessionLogger(object):
    def __init__(self, filename='session_log.csv'):
        self.lock = threading.Lock()
        is_new = not os.path.exists(filename)

        # File handle: binary on Py2, newline='' text on Py3
        if PY2:
            # Py2 csv expects bytes
            self.csvfile = open(filename, 'ab')
        else:
            # Py3 csv expects text and needs newline='' to avoid blank lines
            self.csvfile = open(filename, 'a', newline='', encoding='utf-8')

        self.fieldnames = [
            'session_id',
            'epoch_ts',
            'iso_ts',
            'exercise',
            'token',
            'response_time_s',
            'asr_confidence',
            'emotion_label',
            'emotion_confidence',
            'task_completion_rate',
            'sustained_attention_s',
            'adaptation_events',
            'successful_adaptations',
            'summary_flag'
        ]

        # Writer (no dialect tweaks needed)
        self.writer = csv.DictWriter(self.csvfile, fieldnames=self.fieldnames)

        # Write header if file is new/empty
        if is_new or os.path.getsize(filename) == 0:
            self._write_header()

    # ---------- internal helpers ----------

    def _to_bytes_py2(self, v):
        """Return a UTF-8 encoded byte-string for csv on Py2."""
        if v is None:
            return ''
        # Already a byte-string?
        if isinstance(v, str):
            try:
                # ensure it's valid as-is; if not, fall through to encode
                v.decode('utf-8')
                return v
            except Exception:
                pass
        # Unicode or other types → convert to unicode then encode
        try:
            # unicode exists on Py2
            if isinstance(v, unicode):  # noqa: F821  (valid on Py2)
                return v.encode('utf-8')
        except NameError:
            pass
        try:
            # numbers, floats, bools, objects with __unicode__/__str__
            return unicode(v).encode('utf-8')  # noqa: F821
        except Exception:
            return str(v)

    def _sanitize_row(self, rowdict):
        """Return a dict appropriate for the runtime's csv writer."""
        if PY2:
            # csv on Py2 requires bytes for all values
            return {k: self._to_bytes_py2(rowdict.get(k, '')) for k in self.fieldnames}
        else:
            # Py3 wants text (str); ensure everything is str
            def _to_str(v):
                if v is None:
                    return ''
                if isinstance(v, str):
                    return v
                return str(v)
            return {k: _to_str(rowdict.get(k, '')) for k in self.fieldnames}

    def _write_header(self):
        with self.lock:
            # DictWriter.writeheader uses the fieldnames; they are ASCII-safe
            self.writer.writeheader()
            self.csvfile.flush()

    # ---------- public API ----------

    def log_turn(self, **kwargs):
        """
        Accepts exactly the keys in the header as kwargs.
        Missing values default to ''.
        """
        row = {col: kwargs.get(col, '') for col in self.fieldnames}
        safe = self._sanitize_row(row)
        with self.lock:
            self.writer.writerow(safe)
            self.csvfile.flush()

    def close(self):
        try:
            self.csvfile.close()
        except Exception:
            pass
