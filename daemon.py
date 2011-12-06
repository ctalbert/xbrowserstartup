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
import urllib
import Queue
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
    def __init__(self, is_restarting=False, cachefile="daemon_cache.ini", testconfig="test_config.ini", port=28001):
        self._stop = False
        self._cache = cachefile
        self._phonemap = {}
        self._lasttest = datetime(2011, 10, 22)
        self._cachelock = threading.RLock()
        # TODO: Make this configurable
        self._logfilename = "daemon.log"
        self._logfile = open(self._logfilename, "w")

        self.log("Starting Daemon")
        # Start the queue
        self._jobs = Queue.Queue()

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
        self.pulsemonitor = start_pulse_monitor(buildCallback=self.on_build,
                                                tree=["birch"],
                                                platform=["linux-android"],
                                                mobile=False,
                                                buildtype="opt"
                                               )

        nettools = NetworkTools()
        ip = nettools.getLanIp()

        self.server = CmdThreadedTCPServer((ip, int(port)), CmdTCPHandler)
        server_thread = threading.Thread(target=self.server.serve_forever)
        server_thread.setDaemon(True)
        server_thread.start()

    def msg_loop(self):
        try:
            while (not self._stop):
                sleep(60)
                while not self._jobs.empty():
                    self.lock_and_run_tests()

        except KeyboardInterrupt:
            self.server.shutdown()

    # This runs the tests and resets the self._lasttest variable.
    # It also can install a new build, to install, set build_url to the URL of the
    # build to download and install
    def lock_and_run_tests(self, build_url=None):
        try:
            job = self._jobs.get()
            if "buildurl" in job:
                res = self.install_build(job["phone"], job["buildurl"])
                if res:
                    self.run_tests(job)
                else:
                    self.log("Failed to install: Phone:%s Build%s" % (job["phone"], job["buildurl"]),
                             isError=True)
            else:
                self.run_tests(job)
        except:
            self.log("Exception: %s %s" % sys.exc_info()[:2], isError=True)
        finally:
            self._jobs.task_done()
            self.log("Test Lock Released")

    def route_cmd(self, conn, data):
        regdeviceRE = re.compile('register.*')
        data = data.lower()
        if not conn:
            self.log("Lost Daemon connection!", isError=True)
            raise DaemonException("Lost Connection")

        if data == 'stop':
            self.stop()
        elif regdeviceRE.match(data):
            conn.send("OK\r\n")
            self.register_device(data)
        elif data == 'quit':
            self.log("Daemon received quit notification", isError=True)
            return True
        else:
            conn.send("Unknown command, either stop or register device\n")
        return False

    def register_device(self, data):
        # Eat register command
        data = data.lstrip("register ")

        # Un-url encode it
        data = urlparse.parse_qs(data)

        # Lock down so we write to cache safely
        self._cachelock.acquire()
        self.log("Obtained cachelock for registering: %s" % data)

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
            self.log("Released cachelock")

    def read_cache(self):
        # Being a little paranoid
        self._cachelock.acquire()
        self.log("Read_cache::cachelock acquired: %s" % self._cache)
        try:
            self._phonemap.clear()
            cfg = ConfigParser.RawConfigParser()
            cfg.read(self._cache)
            for i in cfg.items("phones"):
                vlist = i[1].split(',')
                self._phonemap[i[0]] = {"ip": vlist[0],
                                        "name": vlist[1],
                                        "port": vlist[2]}
            
            # Jobs are a string of key/value pairs and have the following attributes:
            # phone=<phonemacaddress>,buildurl=<url>,builddate=<builddate>,revision=<revision>,
            # test=<testname>,iterations=<iterations>
            #
            if cfg.has_section("jobs"):
                for i in cfg.items("jobs"):
                    vlist = i[1].split(',')
                    job = {}
                    for v in vlist:
                        k = v.split("=")
                        # Insert the key value pairs into the dict
                        job[k[0]] = k[1]
                    # Insert the full job dict into the queue for processing
                    self.log("Adding job: %s" % job)
                    self._jobs.put_nowait(job)   
        except:
            self.log("Unable to rebuild cache: %s %s" % sys.exc_info()[:2], isError=True)
            # We may not have started the server yet.
            if self.server:
                self.stop()
            else:
                sys.exit(1)
        finally:
            self._cachelock.release()
            self.log("Read_cache::cachelock released")

    def reset_phones(self):
        nt = NetworkTools()
        myip = nt.getLanIp()
        for k,v in self._phonemap.iteritems():
            self.log("Rebooting %s:%s" % (k, v["name"]))

            try:
                dm = devicemanagerSUT.DeviceManagerSUT(v["ip"],v["port"])
                dm.reboot(myip)
            except:
                self.log("COULD NOT REBOOT PHONE: %s:%s" % (k, v["name"]),
                        isError=True)
                # TODO: SHould it get removed from the list? Think so.
                #del self._phonemap[k]

    def on_build(self, msg):
        # Use the msg to get the build and install it then kick off our tests
        self.log("---------- BUILD FOUND ----------")
        self.log("%s" % msg)
        self.log("---------------------------------")

        # We will get a msg on busted builds with no URLs, so just ignore
        # those, and only run the ones with real URLs
        # We create jobs for all the phones and push them into the queue
        if "buildurl" in msg:
            for k,v in self._phonemap.iteritems():
                job = {"phone":k, "buildurl":msg["buildurl"], "builddate":msg["builddate"],
                       "revision":msg["commit"]}
                self._jobs.put_nowait(job)
            
    # Install a build on a phone
    # phoneID: phone mac address
    # url: url of build to download and install
    def install_build(self, phoneID, url):
        ret = True
        # First, you download
        try:
            buildfile = os.path.abspath("fennecbld.apk")
            urllib.urlretrieve(url, buildfile)
        except:
            self.log("Could not download nightly due to: %s %s" % sys.exc_info()[:2],
                    isError=True)
            ret = False

        nt = NetworkTools()
        myip = nt.getLanIp()

        if ret:
            try:
                dm = devicemanagerSUT.DeviceManagerSUT(self._phonemap[phoneID]["ip"],
                                                       self._phonemap[phoneID]["port"])
                devroot = dm.getDeviceRoot()
                # If we get a real deviceroot, then we can connect to the phone
                if devroot:
                    devpath = devroot + "/fennecbld.apk"
                    dm.pushFile("fennecbld.apk", devpath)
                    dm.updateApp(devpath, processName="org.mozilla.fennec", ipAddr=myip)
            except:
                self.log("Could not install latest nightly on %s:%s" % (k,v["name"]), isError=True)
                self.log("Exception: %s %s" % sys.exc_info()[:2], isError=True)
                ret = False

        # If the file exists, clean it up
        if os.path.exists("fennecbld.apk"):
            os.remove("fennecbld.apk")
        return ret

    def run_tests(self, job):
        # TODO: We can make this configurable by reading in a list of
        #       test classes that will conform to this pattern
        # Need a way to figure out how to do the imports though

        try:

            import runstartuptest
            phoneID = job["phone"]
            phoneName = self._phonemap[phoneID]["name"]
            self.log("*!*!*! Beginning test run on %s:%s *!*!*!"% (phoneID, phoneName))

            # Configure it
            # Add in a revision ID into our config file for this test run
            cfile = phoneName + ".ini"
            cfg = ConfigParser.RawConfigParser()
            cfg.read(cfile)
            cfg.set("options", "revision", job["revision"])
            cfg.set("options", "builddate", job["builddate"])
            cfg.write(open(cfile, 'w'))

            opts = {"configfile": cfile}
            testopts = runstartuptest.StartupOptions()
            opts = testopts.verify_options(opts)
            dm = devicemanagerSUT.DeviceManagerSUT(self._phonemap[phoneID]["ip"],
                                                   self._phonemap[phoneID]["port"])

            # Run it
            # TODO: At the moment, hack in support for allowing it to
            #       log to our logging method.
            t = runstartuptest.StartupTest(dm, opts, logcallback=self.log)
            t.prepare_phone()
            t.run()
        except:
            t, v, tb = sys.exc_info()
            self.log("Test Run threw exception: %s %s" % (t,v), isError=True)
            traceback.print_exception(t,v,tb)

    def log(self, msg, isError=False):
        timestamp = datetime.now().isoformat("T")
        if not self._logfile:
            self._logfile = open(self._logfilename, "w")
            m = "ERROR|Reopening Log File|%s\n" % timestamp
            print m
            self._logfile.write(m)

        if isError:
            m = "ERROR|%s|%s\n" % (msg, timestamp)
        else:
            m = "INFO|%s|%s\n" % (msg, timestamp)

        # TODO: Defaults to being very chatty
        print m
        self._logfile.write(m)
        self._logfile.flush()

    def stop(self):
        self._stop = True
        self._logfile.close()
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
