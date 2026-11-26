import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.http_common import make_vercel_handler  # noqa: E402


# Vercel needs a literal `handler` class here. Do not add an `app` or
# `application` alias: Vercel then treats it as WSGI/ASGI and the function crashes.
class handler(make_vercel_handler("/api/prioritise")):
    pass
