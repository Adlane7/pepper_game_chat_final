# -*- coding: utf-8 -*-
import qi

def _qi_async(fn, *args, **kwargs):
    # Avoid writing qi.async directly (Py3 linters)
    async_fn = getattr(qi, 'async', None) or getattr(qi, 'async_', None)
    if async_fn is None:
        raise AttributeError("qi async function not found")
    return async_fn(fn, *args, **kwargs)

class TabletSafe(object):
    def __init__(self, session=None, tablet_proxy=None):
        """
        Provide either:
          - session: qi.Session  (we'll fetch ALTabletService)
          - tablet_proxy: an existing ALTabletService proxy
        """
        if tablet_proxy is not None:
            self.tablet = tablet_proxy
        elif session is not None:
            self.tablet = session.service("ALTabletService")
        else:
            raise ValueError("TabletSafe needs a qi.Session or an ALTabletService proxy.")

    def ensure_ready(self, timeout=1.0):
        fut = _qi_async(self.tablet.showWebview, "about:blank")
        try:
            fut.wait(timeout); return True
        except Exception:
            try: fut.cancel()
            except Exception: pass
            return False

    def show_image(self, url, timeout=2.0):
        fut = _qi_async(self.tablet.showImage, url)
        try:
            fut.wait(timeout); return True
        except Exception:
            try: fut.cancel()
            except Exception: pass
            return False

    def show_url(self, url, timeout=2.0):
        fut = _qi_async(self.tablet.showWebview, url)
        try:
            fut.wait(timeout); return True
        except Exception:
            try: fut.cancel()
            except Exception: pass
            return False

    def show_url_or_image(self, url, timeout=2.0):
        # Try showImage first; if that fails, open the URL in the webview
        if self.show_image(url, timeout=timeout):
            return True
        return self.show_url(url, timeout=timeout)

    def show_blank(self, timeout=1.0):
        fut = _qi_async(self.tablet.showWebview, "about:blank")
        try:
            fut.wait(timeout); return True
        except Exception:
            try: fut.cancel()
            except Exception: pass
            return False

    def hide(self):
        try:
            # Use hideImage if an image is shown; else hideWebview
            self.tablet.hideImage()
        except Exception:
            try:
                self.tablet.hideWebview()
            except Exception:
                pass
