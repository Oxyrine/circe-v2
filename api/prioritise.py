import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.http_common import make_vercel_handler

handler = make_vercel_handler("/api/prioritise")
app = handler
application = handler
