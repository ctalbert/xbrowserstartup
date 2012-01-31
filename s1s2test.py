import re
import os
import threading
import androidutils
import ConfigParser
import json
import urllib2
from time import sleep
from phonetest import PhoneTest
from devicemanagerSUT import DeviceManagerSUT

CONFIG_FILE_PATH = os.path.join(os.getcwd(), "s1s2_settings.ini")

class S1S2Test(PhoneTest):
    def __init__(self,
                 phoneid=None,
                 serial=None,
                 ip=None,
                 sutcmdport=None,
                 sutdataport=None,
                 osver=None):
        PhoneTest.__init__(self, phoneid, serial, ip, sutcmdport, sutdataport, osver)

    def add_job(self, job):
        self._logger.info("s1s2test adding job: %s" % job)
        self._jobs.put_nowait(job)

    def start_test(self, stop=False):
        # For Android, adb expects our serial number to be in upper case
        self._serial = self._serial.swapcase()
        self.stop = stop
        self._thread = threading.Thread(target=self.runtests,
                name=self._phoneid)
        self._thread.start()

    def runtests(self):
        # Ensure we have a connection to the device
        self.dm = DeviceManagerSUT(self._ip, self._sutcmdport)
        # Get our next job
        while 1:
            if self._jobs.empty() and self.stop:
                # Then we are finished and we should end the thread
                break

            # This blocks until a job arrives
            job = self._jobs.get()
            self._logger.debug("Got job: %s" % job)
            if ("buildurl" not in job or "androidprocname" not in job or
                "revision" not in job or "blddate" not in job or
                "buildtype" not in job or "version" not in job):
                self._logger.error("Invalid job configuration: %s" % job)
                raise NameError("ERROR: Invalid job configuration: %s" % job)
                
            androidutils.install_build_adb(phoneid = self._phoneid,
                                           url=job["buildurl"],
                                           procname = job["androidprocname"],
                                           serial=self._serial)

            # Read our config file which gives us our number of
            # iterations and urls that we will be testing
            self.prepare_phone(job)

            intent = job["androidprocname"] + "/.App"

            for u in self._urls:
                self._logger.info("Running url %s for %s iterations" %
                        (u, self._iterations))
                for i in range(self._iterations):
                    # Set status
                    self._set_status(msg="Run %s for url %s" % (i,u))

                    # Clear logcat
                    androidutils.run_adb("logcat", ["-c"], self._serial)

                    # Get start time
                    starttime = self.dm.getInfo('uptimemillis')['uptimemillis'][0]

                    # Run test
                    androidutils.run_adb("shell",
                            ["sh", "/mnt/sdcard/s1test/runbrowser.sh", intent,
                                u], self._serial)

                    # Let browser stabilize
                    sleep(5)

                    # Get results
                    throbberstart, throbberstop, drawtime = self.analyze_logcat()

                    # Publish results
                    self.publish_results(starttime=int(starttime),
                                         tstrt=throbberstart,
                                         tstop=throbberstop,
                                         drawing=drawtime,
                                         job = job,
                                         url = u)
                    androidutils.kill_proc_sut(self._ip, self._sutcmdport,
                            job["androidprocname"])
                    androidutils.remove_sessionstore_files_adb(self._serial,
                            procname=job["androidprocname"])

            self._jobs.task_done()
            self._logger.debug("Finished job: %s" % job)

    def prepare_phone(self, job):
        print "Preparing phone"
        androidutils.run_adb("shell", ["mkdir", "/mnt/sdcard/s1test"],
                self._serial)
        androidutils.run_adb("push", ["runbrowser.sh",
            "/mnt/sdcard/s1test/"], self._serial)

        testroot = "/mnt/sdcard/s1test"
        
        if not os.path.exists(CONFIG_FILE_PATH):
            self._logger.error("Cannot find config file: %s" % CONFIG_FILE_PATH)
            raise NameError("Cannot find config file: %s" % CONFIG_FILE_PATH)
        
        cfg = ConfigParser.RawConfigParser()
        cfg.read(CONFIG_FILE_PATH)
        
        # Map URLS - {urlname: url} - urlname serves as testname
        self._urls = {}
        for u in cfg.items("urls"):
            self._urls[u[0]] = u[1]

        # Move the local html files in htmlfiles onto the phone's sdcard
        # Copy our HTML files for local use into place
        # TODO: Handle errors       
        for h in cfg.items("htmlfiles"):
            androidutils.run_adb("push", [h[1], testroot + "/%s" % h[1]], self._serial)
        
        self._iterations = cfg.getint("settings", "iterations")
        self._resulturl = cfg.get("settings", "resulturl")
        
        
 
    def analyze_logcat(self):
        buf = androidutils.run_adb("logcat", ["-d"], self._serial)
        buf = buf.split('\r\n')
        throbberstartRE = re.compile(".*Throbber start$")
        throbberstopRE = re.compile(".*Throbber stop$")
        endDrawingRE = re.compile(".*endDrawing$")
        throbstart = 0
        throbstop = 0
        enddraw = 0

        for line in buf:
            line = line.strip()
            if throbberstartRE.match(line):
                throbstart = line.split(' ')[-4]
            elif throbberstopRE.match(line):
                throbstop = line.split(' ')[-4]
            elif endDrawingRE.match(line):
                enddraw = line.split(' ')[-3]
        return (int(throbstart), int(throbstop), int(enddraw))

    def publish_results(self, starttime=0, tstrt=0, tstop=0, drawing=0, job=None, url = ''):
        msg = "Start Time: %s Throbber Start: %s Throbber Stop: %s EndDraw: %s" % (starttime, tstrt, tstop, drawing)
        print "RESULTS %s:%s" % (self._phoneid, msg)
        self._logger.info("RESULTS: %s:%s" % (self._phoneid, msg))
        
        # Create JSON to send to webserver
        resultdata = {}
        resultdata["phoneid"] = self._phoneid
        resultdata["testname"] = url
        resultdata["throbberstart"] = tstrt
        resultdata["throbberstop"] = tstop
        resultdata["enddrawing"] = drawing
        
        # Accept either YYYY-mm-dd or YYYY-mm-ddTHH:MM:SS for blddate stamps
        # TODO: Enforce these formats and throw errors if not found
        dt = job["blddate"].split("T")
        if len(dt) == 1:
            # YYYY-mm-dd format
            resultdata["blddate"] = "%sT%s", (job["blddate"], "00:00:00")
        else:
            resultdata["blddate"] = job["blddate"]
        
        resultdata["revision"] = job["revision"]
        resultdata["productname"] = job["androidprocname"]
        resultdata["productversion"] = job["version"]
        resultdata["osver"] = self._osver
        
        # Upload
        result = json.dumps({"data": resultdata})
        req = urllib2.Request(self._resulturl, result, {'Content-Type': 'application/json'})
        f = urllib2.urlopen(req)
        response = f.read()
        f.close()
        
        
        
        
        
        
        
        



