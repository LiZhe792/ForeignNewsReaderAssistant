"""Request timings without article text, credentials, bodies or query strings."""
import json
import time
import uuid
from flask import g, request


def install(app):
    @app.before_request
    def start():
        g.request_started=time.perf_counter();g.request_id=uuid.uuid4().hex

    @app.after_request
    def finish(response):
        elapsed=round((time.perf_counter()-getattr(g,'request_started',time.perf_counter()))*1000,1)
        response.headers['X-Request-ID']=getattr(g,'request_id','')
        response.headers['Server-Timing']='application;dur='+str(elapsed)
        if elapsed>=1000 or response.status_code>=500:
            app.logger.warning(json.dumps({'event':'slow_or_failed_request','request_id':getattr(g,'request_id',''),
                                           'route':str(request.url_rule or 'unmatched'),'method':request.method,
                                           'status':response.status_code,'duration_ms':elapsed}))
        return response
