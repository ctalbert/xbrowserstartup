import socket
import sys, os
import threading
from time import sleep
from datetime import datetime, timedelta
from optparse import OptionParser
import ConfigParser
import urlparse
import SocketServer
import re
import urllib2
import traceback
import uuid
from pulsebuildmonitor import start_pulse_monitor
import devicemanager, devicemanagerSUT
from devicemanager import NetworkTools

# Objects that conform to test object interface
import runstartuptest


gDaemon = None

class CmdThreadedTCPServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer): 
    pass

class CmdTCPHandler(SocketServer.BaseRequestHandler):

    def handle(self):
        self.request.send('>')
        data = self.request.recv(1024).strip()
        while (data):
            closeConn = gDaemon.route_cmd(self.request, data)
            if (closeConn):
                data = ''
                continue

            self.request.send('>')
            data = self.request.recv(1024)
            if (data):
                data = data.strip()
            else: 
                data = ''

class Daemon():
    def __init__(self, is_restarting=False, cachefile="daemon_cache.ini", port=28001):
        self._stop = False
        self._cache = cachefile
        self._phonemap = {}
        #TODO: might not need this. self._testrunning = False
        self._lasttest = datetime(2011, 10, 22)
        self._lock = threading.RLock()
        self._cachelock = threading.RLock()

        if not os.path.exists(self._cache):
            # If we don't have a cache you aren't restarting
            is_restarting = False
            open(self._cache, 'wb')
        elif not is_restarting:
            # If we have a cache and we are NOT restarting, then assume that
            # cache is invalid. Blow it away and recreate it
            os.remove(self._cache)
            open(self._cache, 'wb')

        if is_restarting:
            self.read_cache()
            self.reset_phones()
  
        # Start our pulse listener for the birch builds
        # TODO: Causing deadlocks, moving out of process...
        # In the meantime, pick up builds from the check_for_build method
        #self.pulsemonitor = start_pulse_monitor(buildCallback=self.on_build,
        #                                        tree=["birch"],
        #                                        platform=["linux-android"],
        #                                        mobile=False,
        #                                        buildtype="opt"
        #                                       )

        nettools = NetworkTools()
        ip = nettools.getLanIp()
        
        self.server = CmdThreadedTCPServer((ip, int(port)), CmdTCPHandler)
        server_thread = threading.Thread(target=self.server.serve_forever)
        server_thread.setDaemon(True)
        server_thread.start()
       
    def msg_loop(self):
        try:
            while (not self._stop):
                sleep(10)
                # Run the tests if it's been more than two hours since the last run.
                # lock_and_run_tests will reset our _lasttest variable
                if (datetime.now() - self._lasttest) > timedelta(seconds=7200):
                    self.lock_and_run_tests()

        except KeyboardInterrupt:
            self.server.shutdown()

    # This runs the tests and resets the self._lasttest variable.
    # It also can install a new build, to install, set build_url to the URL of the
    # build to download and install
    def lock_and_run_tests(self, build_url=None):
        try:
            self._lock.acquire()
            build_url = self.check_for_build()
            if build_url:
                self.install_build(build_url)
            self.run_tests()
        except:
            print "Exception: %s %s" % sys.exc_info()[:2]
        finally:
            self._lock.release()
            self._lasttest = datetime.now()

    def check_for_build(self):
        # Work around to keep us working until we can debug pulse issue
        # It has some terrible hardcoded magic, don't look
        # Depends on a file called builds.ini to get the URL and
        # to track whether that build was installed or not.
        try:
            cfg = ConfigParser.RawConfigParser()
            cfg.read("builds.ini")
            if cfg.has_section("builds"):
                url = cfg.get("builds", "url")
                isused = cfg.get("builds", "installed")
                if isused == "0":
                    cfg.set("builds", "installed", 1)
                    cfg.write(open("builds.ini", "wb"))
                    return url
                return None
        except:
            print "Could not read builds.ini: %s %s" % sys.exc_info()[:2]
        return None

    def route_cmd(self, conn, data):
        regdeviceRE = re.compile('register.*')
        data = data.lower()
        if not conn:
            raise DaemonException("Lost Connection")
        
        if data == 'stop':
            self.stop()
        elif regdeviceRE.match(data):
            conn.send("OK\r\n")
            self.register_device(data)
        elif data == 'quit':
            return True
        else:
            conn.send("Unknown command, either stop or register device\n")
        return False
    
    def register_device(self, data):
        # Do not accept registrations when running tests - nothing wrong with it,
        # but it keeps things simpler this way, less chance for things to go wrong.
        #TODO: needed? if self._testrunning:
        #    return
        
        # Eat register command
        data = data.lstrip("register ")
        
        # Un-url encode it
        data = urlparse.parse_qs(data)
        
        print "Registering phone: %s" % data
        
        # Lock down so we write to cache safely
        self._cachelock.acquire()
        try:
            # Map MAC Address to ip and user name for phone
            # Even if a known phone is re-registering, just overwrite its record
            # in case its IP changed
            # The configparser does odd things with the :'s so remove them.
            macaddy = data['name'][0].replace(':', '_')
            self._phonemap[macaddy] = {'ip': data['ipaddr'][0],
                                       'name': data['hardware'][0],
                                       'port': data['cmdport'][0]}
            cfg = ConfigParser.RawConfigParser()
            cfg.read(self._cache)
            if not cfg.has_section("phones"):
                cfg.add_section("phones")
            
            values = "%s,%s,%s" % (self._phonemap[macaddy]['ip'],
                                   self._phonemap[macaddy]['name'],
                                   self._phonemap[macaddy]['port'])
            cfg.set("phones", macaddy, values)
            cfg.write(open(self._cache, 'wb'))
        except:
            print "ERROR: could not write cache file, exiting"
            print "Exception: %s %s" % sys.exc_info()[:2]
            self.stop()
        finally:
            self._cachelock.release()

    def read_cache(self):
        print "reading cache: %s" % self._cache
        # Being a little paranoid
        self._cachelock.acquire()
        try:
            self._phonemap.clear()
            cfg = ConfigParser.RawConfigParser()
            cfg.read(self._cache)
            for i in cfg.items("phones"):
                vlist = i[1].split(',')
                self._phonemap[i[0]] = {"ip": vlist[0],
                                        "name": vlist[1],
                                        "port": vlist[2]}
        except:
            print "Error: Unable to rebuild cache, exiting"
            print "Exception: %s %s" % sys.exc_info()[:2]
            # We may not have started the server yet.
            if self.server:
                self.stop()
            else:
                sys.exit(1)
        finally:
            self._cachelock.release()
    
    def reset_phones(self):
        nt = NetworkTools()
        myip = nt.getLanIp()
        for k,v in self._phonemap.iteritems():
            print "Rebooting phone %s:%s" % (k, v["name"])
            try:
                dm = devicemanagerSUT.DeviceManagerSUT(v["ip"],v["port"])
                dm.reboot(myip)
            except:
                print "Could not reboot phone %s:%s" % (k, v["name"])
                # TODO: SHould it get removed from the list? Think so.
                del self._phonemap[k]
        
    def on_build(self, msg):
        # Use the msg to get the build and install it then kick off our tests
        print "---------- BUILD FOUND ----------"
        print "%s" % msg
        print "---------------------------------"

        # We will get a msg on busted builds with no URLs, so just ignore
        # those, and only run the ones with real URLs
        if "buildurl" in msg:
            url = msg["buildurl"]
            self.lock_and_run_tests(build_url=url)

    def install_build(self, url):
        # First, you download
        try:
            resp = urllib2.urlopen(url)
            apk = resp.read()
            f = open("fennecbld.apk", "wb")
            f.write(apk)
            f.close()
        except:
            print "Could not download nightly due to: %s %s" % sys.exc_info()[:2]
            self.stop()

        nt = NetworkTools()
        myip = nt.getLanIp()

        for k,v in self._phonemap.iteritems():
            try:
                dm = devicemanagerSUT.DeviceManagerSUT(v["ip"], v["port"])
                devpath = dm.getDeviceRoot() + "/fennecbld.apk"
                dm.pushFile("fennecbld.apk", devpath)
                dm.updateApp(devpath, processName="org.mozilla.fennec", ipAddr=myip)
            except:
                print "Could not install latest nightly on %s:%s" % (k, v["name"])
                print "Exception: %s %s" % sys.exc_info()[:2]
                self.stop()
        
        # If the file exists, clean it up
        if os.path.exists("fennecbld.apk"):
            os.remove("fennecbld.apk")
    
    def run_tests(self):
        # TODO: We can make this configurable by reading in a list of
        #       test classes that will conform to this pattern
        # Need a way to figure out how to do the imports though

        revisionguid = uuid.uuid1()
        try:

            import runstartuptest

            for k,v in self._phonemap.iteritems():
                print "*!*!*!*!*! Running startup test on %s:%s *!*!*!*!*!" % (k, v["name"])

                # Configure it
                # Add in a revision ID into our config file for this test run
                cfile = v["name"] + ".ini"
                cfg = ConfigParser.RawConfigParser()
                cfg.read(cfile)
                cfg.set("options", "revision", revisionguid)
                cfg.write(open(cfile, 'w'))
            
                opts = {"configfile": cfile}
                testopts = runstartuptest.StartupOptions()
                opts = testopts.verify_options(opts)
                dm = devicemanagerSUT.DeviceManagerSUT(v["ip"], v["port"])
                
                # Run it
                t = runstartuptest.StartupTest(dm, opts)
                t.prepare_phone()
                t.run()
        except:
            t, v, tb = sys.exc_info()
            print "Test Run threw exception: %s %s" % (t,v)
            traceback.print_exception(t,v,tb)
        finally:
            # Reboot the phones
            self.reset_phones()

    def stop(self):
        self._stop = True
        self.server.shutdown()

def main(is_restarting, cachefile, port):
    global gDaemon
    gDaemon = Daemon(is_restarting=is_restarting,
                     cachefile = cachefile,
                     port = port)
    gDaemon.msg_loop()

defaults = {}
parser = OptionParser()
parser.add_option("--restarting", action="store_true", dest="is_restarting",
                  help="If specified, we restart using the information in cache")
defaults["is_restarting"] = False

parser.add_option("--port", action="store", type="string", dest="port",
                  help="Port to listen for incoming connections, defaults to 28001")
defaults["port"] = 28001

parser.add_option("--cache", action="store", type="string", dest="cachefile",
                  help="Cache file to use, defaults to daemon_cache.ini in local dir")
defaults["cachefile"] = "daemon_cache.ini"

parser.set_defaults(**defaults)
(options, args) = parser.parse_args()

if __name__ == "__main__":
    main(options.is_restarting, options.cachefile, options.port)          
            
        
   
