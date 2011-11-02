import templeton.handlers
import json

# URLs go here. "/api/" will be automatically prepended to each.
urls = ('/tsresults/', 'TsResults'
)

# Handler classes go here
class TsResults(object):
    def POST(self):
        print "HELLO"
        if PROXY_TO:
            print "NEED PROXY"
        web_data = json.loads(web.data())
        print "GOT data: %s" % web_data

    @templeton.handlers.json_response
    def GET(self):
        args, body = templeton.handlers.get_request_parms()
        print "args: %s" % args
        print "body: %s" % body
        return {'ok': 200}


