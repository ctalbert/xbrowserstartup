import socket
import logging
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
from devicemanager import NetworkTools
from devicemanagerSUT import DeviceManagerSUT

# Objects that conform to test object interface
# TODO: refactor this one: import runstartuptest
from s1s2test import S1S2Test

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
    def __init__(self, is_restarting=False, cachefile="daemon_cache.ini",
            testconfig="test_config.ini", port=28001, logfile="daemon.log",
            loglevel="DEBUG"):
        self._stop = False
        self._cache = cachefile
        self._phonemap = {}
        self._cachelock = threading.RLock()
        logging.basicConfig(filename=logfile,
                            filemode="w",
                            level=loglevel,
                            format='%(asctime)s|%(levelname)s|%(message)s')

        logging.info("Starting Daemon")
        # Start the queue
        self._jobs = Queue.Queue()
        self._phonesstarted = False

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
                self.disperse_jobs()
                self.start_phones()
                sleep(60)

        except KeyboardInterrupt:
            self.server.shutdown()

    # This runs the tests and resets the self._lasttest variable.
    # It also can install a new build, to install, set build_url to the URL of the
    # build to download and install
    def disperse_jobs(self):
        try:
            logging.debug("Asking for jobs")
            while not self._jobs.empty():
                job = self._jobs.get()
                logging.debug("Got job: %s" % job)
                for k,v in self._phonemap.iteritems():
                    # TODO: Refactor so that the job can specify the test so that
                    # then multiple types of test objects can be ran on one set of
                    # phones.
                    logging.debug("Adding job to phone: %s" % v["name"])
                    v["testobj"].add_job(job)
                self._jobs.task_done()
        except:
            logging.error("Exception adding jobs: %s %s" % sys.exc_info()[:2])

    # Start the phones for testing
    def start_phones(self):
        if not self._phonesstarted:
            for k,v in self._phonemap.iteritems():
                logging.info("Starting phone: %s" % v["name"])
                v["testobj"].start_test()
            self._phonesstarted = True


    def route_cmd(self, conn, data):
        regdeviceRE = re.compile('register.*')
        data = data.lower()
        if not conn:
            logging.debug("Lost Daemon connection!", isError=True)
            raise DaemonException("Lost Connection")

        # TODO: Implement the get status command to get status for a particular
        # phone
        if data == 'stop':
            self.stop()
        elif regdeviceRE.match(data):
            conn.send("OK\r\n")
            self.register_device(data)
        elif data == 'quit':
            logging.debug("Daemon received quit notification", isError=True)
            return True
        else:
            conn.send("Unknown command, either stop or register device\n")
        return False

    def _create_test_object(self, macaddy, phonedict):
        t = S1S2Test(phoneid=macaddy + "_" + phonedict['name'],
                     serial = phonedict['serial'],
                     ip = phonedict['ip'],
                     sutcmdport = phonedict['port'],
                     machinetype = phonedict['name'],
                     osver = phonedict['os'])
        return t

    def register_device(self, data):
        # Eat register command
        data = data.lstrip("register ")

        # Un-url encode it
        data = urlparse.parse_qs(data)

        # Lock down so we write to cache safely
        self._cachelock.acquire()
        logging.debug("Obtained cachelock for registering: %s" % data)
        try:
            # Map MAC Address to ip and user name for phone
            # The configparser does odd things with the :'s so remove them.
            macaddy = data['name'][0].replace(':', '_')

            if macaddy not in self._phonemap:
                self._phonemap[macaddy] = {'ip': data['ipaddr'][0],
                                           'name': data['hardware'][0],
                                           'port': data['cmdport'][0],
                                           'serial': data['pool'][0],
                                           'os': data['os'][0]}
                testobj = self._create_test_object(macaddy, self._phonemap[macaddy])
                self._phonemap[macaddy]["testobj"] = testobj


                cfg = ConfigParser.RawConfigParser()
                cfg.read(self._cache)
                if not cfg.has_section("phones"):
                    cfg.add_section("phones")

                values = "%s,%s,%s,%s,%s" % (self._phonemap[macaddy]['ip'],
                                          self._phonemap[macaddy]['name'],
                                          self._phonemap[macaddy]['port'],
                                          self._phonemap[macaddy]['serial'],
                                          self._phonemap[macaddy]['os'])
                logging.debug("Registering new phone: %s" % values)
                cfg.set("phones", macaddy, values)
                cfg.write(open(self._cache, 'wb'))
            else:
                logging.debug("Registering known phone: %s_%s" % (data['name'],
                        data['hardware']))
        except:
            print "ERROR: could not write cache file, exiting"
            print "Exception: %s %s" % sys.exc_info()[:2]
            self.stop()
        finally:
            self._cachelock.release()
            logging.debug("Released cachelock")

    def read_cache(self):
        # Being a little paranoid
        self._cachelock.acquire()
        logging.debug("Read_cache::cachelock acquired: %s" % self._cache)
        try:
            self._phonemap.clear()
            cfg = ConfigParser.RawConfigParser()
            cfg.read(self._cache)
            for i in cfg.items("phones"):
                vlist = i[1].split(',')
                self._phonemap[i[0]] = {"ip": vlist[0],
                                        "name": vlist[1],
                                        "port": vlist[2],
                                        "serial": vlist[3],
                                        "os": vlist[4]}
                testobj = self._create_test_object(i[0], self._phonemap[i[0]])
                self._phonemap[i[0]]["testobj"] = testobj

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
                    logging.info("Adding job: %s" % job)
                    self._jobs.put_nowait(job)
        except:
            logging.error("Unable to rebuild cache: %s %s" % sys.exc_info()[:2])
            # We may not have started the server yet.
            if self.server:
                self.stop()
            else:
                sys.exit(1)
        finally:
            self._cachelock.release()
            logging.debug("Read_cache::cachelock released")

    def reset_phones(self):
        nt = NetworkTools()
        myip = nt.getLanIp()
        for k,v in self._phonemap.iteritems():
            logging.info("Rebooting %s:%s" % (k, v["name"]))

            try:
                dm = DeviceManagerSUT(v["ip"],v["port"])
                dm.reboot(myip)
            except:
                logging.error("COULD NOT REBOOT PHONE: %s:%s" % (k, v["name"]))
                logging.error("exception: %s %s" % sys.exc_info()[:2])

    def on_build(self, msg):
        # Use the msg to get the build and install it then kick off our tests
        logging.debug("---------- BUILD FOUND ----------")
        logging.debug("%s" % msg)
        logging.debug("---------------------------------")

        # We will get a msg on busted builds with no URLs, so just ignore
        # those, and only run the ones with real URLs
        # We create jobs for all the phones and push them into the queue
        if "buildurl" in msg:
            for k,v in self._phonemap.iteritems():
                job = {"phone":k, "buildurl":msg["buildurl"], "builddate":msg["builddate"],
                       "revision":msg["commit"]}
                self._jobs.put_nowait(job)

    def run_tests(self, job):
        # TODO: We can make this configurable by reading in a list of
        #       test classes that will conform to this pattern
        # Need a way to figure out how to do the imports though

        try:

            import runstartuptest
            phoneID = job["phone"]
            phoneName = self._phonemap[phoneID]["name"]
            logging.debug("*!*!*! Beginning test run on %s:%s *!*!*!"% (phoneID, phoneName))

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
            t = runstartuptest.StartupTest(dm, opts, logcallback=logging.debug)
            t.prepare_phone()
            t.run()
        except:
            t, v, tb = sys.exc_info()
            logging.debug("Test Run threw exception: %s %s" % (t,v), isError=True)
            traceback.print_exception(t,v,tb)

    def stop(self):
        self._stop = True
        self.server.shutdown()

def main(is_restarting, cachefile, port, logfile, loglevel):
    global gDaemon
    gDaemon = Daemon(is_restarting=is_restarting,
                     cachefile = cachefile,
                     port = port,
                     logfile = logfile,
                     loglevel = loglevel)
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

parser.add_option("--logfile", action="store", type="string", dest="logfile",
        help="Log file to store logging from entire system, default: daemon.log")
defaults["logfile"] = "daemon.log"

parser.add_option("--loglevel", action="store", type="string", dest="loglevel",
        help="Log level - ERROR, WARNING, DEBUG, or INFO, defaults to DEBUG")
defaults["loglevel"] = "DEBUG"
parser.set_defaults(**defaults)
(options, args) = parser.parse_args()

if __name__ == "__main__":
    main(options.is_restarting, options.cachefile, options.port,
            options.logfile, options.loglevel) 
