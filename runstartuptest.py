from time import sleep
import subprocess
import os
import optparse
import sys
import ConfigParser

import devicemanager,devicemanagerSUT
from devicemanager import NetworkTools

# This class just handles ensuring all our options are sane
class StartupOptions(optparse.OptionParser):
    def __init__(self, configfile=None, **kwargs):
        optparse.OptionParser.__init__(self, **kwargs)
        defaults = {}

        if not configfile:
            self.add_option("--config-file", action="store", type="string", dest="configfile",
                            help="Path to configuration file, if specified then no other options take effect")
            defaults["configfile"] = None
        else:
            defaults["configfile"] = configfile

        self.set_defaults(**defaults)

        usage = """\
python runstartuptest.py --config-file=<configfile.ini> 

You must include a config file with the options you want to run with.  See the
config.ini that lives in the tree to see what the options are and what can be
specificied.  The phone you are running on must be ROOTED and must have the
SUTAgent installed.  It also must have any browsers you want to test installed,
including Native Fennec (if not running in daemon mode).
"""
        self.set_usage(usage)

    def read_config(self, configfile):
        # Reads every option from config file, fails if exepected options are
        # not found in the config file
        config = ConfigParser.RawConfigParser()
        config.read(configfile)
        options = {}
        try:
            u = {}
            for i in config.items("urls"):
                u[i[0]] = i[1]
            options["urls"] = u
        except:
            print "Problem reading in urls from config file: %s %s" % sys.exc_info()[:2]

        try:
            a = {}
            for i in config.items("apps"):
                a[i[0]] = i[1]
            options["apps"] = a
        except:
            print "Problem reading in apps from config file: %s %s" % sys.exc_info()[:2]

        try:
            h = []
            for i in config.items("htmlfiles"):
                h.append(i[1])
            options["htmlfiles"] = h
        except:
            print "Problem reading in htmlfiles from config file: %s %s" % sys.exc_info()[:2]

        try:
            for i in config.items("options"):
                options[i[0]] = i[1]
        except:
            print "Problem reading in main options section from config file: %s %s" % sys.exc_info()[:2]

        if not options["htmldir"]:
            options["htmldir"] = os.getcwd()

        if not options["runtype"]:
            options["runtype"] = ["warm"]
        else:
            options["runtype"] = options["runtype"].split(",")

        if not options["iterations"]:
            options["iterations"] = 10
        else:
            options["iterations"] = int(options["iterations"])

        return options

    def verify_options(self, options):
        if options["configfile"] and os.path.exists(options["configfile"]):
            options = self.read_config(options["configfile"])
        else:
            print "You must specify a config file. See config.ini for allowable fields"
            return False

        if not options["resultsserver"]:
            print "Config file must specify a server IP address for result reporting"
            return False

        if not options["webserver"]:
            print "Config file must specify a web server to run tests against"
            return False

        if not options["phoneid"]:
            print "Config file must specify a phone type for result reporting"
            return False
        else:
            # Ensure no spaces in phone ID
            options["phoneid"] = options["phoneid"].replace(" ","_")
        
        if not options["androidver"]:
            print "You need to input an android version"
            return False

        if not options["sdk"]:
            print "Config file must specify the path to the android sdk"
            return False
        elif not os.path.exists(options["sdk"]):
            print "The android-sdk path specified in config file is incorrect"
            return False

        print "opts: %s" % options
        return options


class StartupTest:
    def __init__(self, dm, options, logcallback=None):
        self.dm = dm
        self.adb = os.path.join(options["sdk"], "platform-tools", "adb")
        self.deviceip = options["deviceip"]
        self.script = options["script"]
        self.timecmd = options["timecmd"]
        self.testroot = options["testroot"]
        self.resultsip = options["resultsserver"]
        self.webserverip = options["webserver"]
        self.phoneid = options["phoneid"]
        self.htmldir = options["htmldir"]
        self.runtype = options["runtype"]
        self.iterations = options["iterations"]
        self.runtype = options["runtype"]
        self.revision = options["revision"]
        self.androidver = options["androidver"]
        self.builddate = options["builddate"]
        self.adb_connected = False
        self.fennec_profile = None

        if options["urls"]:
            self.urls = options["urls"]
        else:
            self.urls = urls

        if options["apps"]:
            self.apps = options["apps"]
        else:
            self.apps = apps

        if options["htmlfiles"]:
            self.htmlfiles = options["htmlfiles"]
        else:
            self.htmlfiles = htmlfiles

        # TODO: Probably should be a python logger
        # This is a method that is called: log(msg, isError=False)
        if logcallback:
            self.log = logcallback
        else:
            self.log = self.backuplogger

    def backuplogger(self, msg, isError=False):
        print msg

    def prepare_phone(self):
        self.log("Preparing Phone")
        try:
            # Create our testroot
            if not self.dm.mkDirs(self.testroot):
                self.log("Could not create directory on phone: %s" % self.testroot)
            else:
                self.log("Created %s" % self.testroot)

            # Copy our time script into place
            if self.dm.pushFile(self.timecmd, "/data/local/%s" % self.timecmd):
                self.log("Copied time")
            else:
                self.log("Failed to copy time binary", isError=True)

            # Chmod our time script - it's overkill but never trust android
            self.dm.launchProcess(["chmod","777", "/data/local/%s" % self.timecmd])
            self.log("chmod process launched")

            # Copy our runscript into place
            if self.dm.pushFile(self.script, self.testroot + "/%s" % self.script):
                self.log("Pushed runscript to %s" % self.testroot)
            else:
                self.log("Failed to push runscript to %s" % self.testroot,
                isError=True)

            # Copy our HTML files for local use into place
            for f in self.htmlfiles:
                if os.path.isdir(f):
                    self.dm.pushDir(f, self.testroot + "/%s" % f)
                else:
                    if self.dm.pushFile(os.path.join(self.htmldir, f), self.testroot + "/%s" % f):
                        self.log("Copied htmlfile %s to %s" % (f, self.testroot))
                    else:
                        self.log("Failed to copy htmlfile %s to %s" % (f,self.testroot),
                                isError=True)
                        
            if not self.adb_connected:
                self._connect_adb()

        except Exception as e:
            self.log("Failed to prepare phone due to %s" % e, isError=True)
            return False
        return True

    def _connect_adb(self):
        # Set up adb over IP
        if self.dm.adb_on(binding="ip"):
            # Connect adb to the device, device defaults to port 5555
            self._run_adb("connect", [self.deviceip])
            self.adbserial = self.deviceip + ":5555"
            self.adb_connected = True
        else:
            self.adb_connected = False
            self.adbserial = None
        return self.adb_connected

    def _get_fennec_profile_path(self):
        if not self.adb_connected:
            connected = self._connect_adb()
            if not connected:
                self.log("ERROR: ADB not connected, failing to get fennec profile", isError=True)
                return None
        
        self.log("Getting Fennec Profile Path")
        data = self._run_adb("shell", ["cat", "/data/data/org.mozilla.fennec/files/mozilla/profiles.ini"],
                      serial=self.adbserial)
        import pdb
        pdb.set_trace()
        pfile = open("profiles.ini", "w")
        pfile.writelines(data.split("\r"))
        pfile.flush()
        path = None
        if os.path.exists("profiles.ini"):
            cfg = ConfigParser.RawConfigParser()
            cfg.read("profiles.ini")
            
            if cfg.has_section("Profile0"):
                isrelative = cfg.get("Profile0", "IsRelative")
                profname = cfg.get("Profile0", "Path")
            else:
                self.log("ERROR: Unknown profile", isError=True)
            if isrelative == "1":
                path = "/data/data/org.mozilla.fennec/files/mozilla/%s" % profname
            else:
                path = profname
        os.remove("profiles.ini")
        return path

    def run(self):
        # Assume the script has been pushed to the phone, set up the path for adb
        phonescript = self.testroot + "/" + os.path.split(self.script)[1]

        for browser, app in self.apps.iteritems():
            for rt in self.runtype:
                for testname, url in self.urls.iteritems():
                    # Amend our testname to indicate our runtype
                    testname = testname + "-" + rt
                    self.log("Running %s with test: %s for %s iterations" %
                            (browser, testname, self.iterations))
                    for i in range(self.iterations):
                        if 'local' in testname:
                            # Then add in testroot as the server location in URL
                            u = url % (self.testroot, self.resultsip, self.phoneid, testname, browser, self.androidver, self.revision, self.builddate)
                        else:
                            # Then add in the webserver as the URL
                            u = url % (self.webserverip, self.resultsip, self.phoneid, testname, browser, self.androidver, self.revision, self.builddate)

                        # Pass in the browser application name so that the
                        # devicemanager knows which process to watch for
                        appname = app.split("/")[0]
                        cmd = ["sh", phonescript, app, u]
                        self.dm.launchProcess(cmd, appnameToCheck=appname)

                        # Give the html 5s to upload results
                        sleep(10)

                        if rt == "cold":
                            # reboot the device between runs
                            print "Rebooting device"
                            nettools = NetworkTools()
                            self.dm.reboot(nettools.getLanIp())
                            print "ok, done with reboot"
                        else:
                            # Then we do a warm startup, killing the process between runs
                            # The name of the process is to the left of the / in the activity manager string
                            self.dm.killProcess(app.split("/")[0])
                        
                        if appname == "org.mozilla.fennec":
                            self.log("Removing session store files from fennec")
                            self._remove_sessionstore_files()

    def _remove_sessionstore_files(self):
        # Get the profile
        if not self.fennec_profile:
            self.fennec_profile = self._get_fennec_profile_path()
        
        if self.adb_connected and self.fennec_profile:
            sessionstorepth = self.fennec_profile + "/sessionstore.js"
            self._run_adb("shell", ["rm", sessionstorepth], serial=self.adbserial)
            sessionstorepth = self.fennec_profile + "/sessionstore.bak"
            self._run_adb("shell", ["rm", sessionstorepth], serial=self.adbserial)
        else:
            self.log("ERROR: Cannot remove sessionstore files", isError=True)

    # cmd must be an array!
    def _run_adb(self, adbcmd, cmd, serial=None):
        if serial:
            self.log("adb cmd: %s" % subprocess.list2cmdline([self.adb, "-s", serial, adbcmd] + cmd))
            p = subprocess.Popen([self.adb, "-s", serial, adbcmd] + cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        else:
            print "run adb cmd: %s" % subprocess.list2cmdline([self.adb, adbcmd] + cmd)
            p = subprocess.Popen([self.adb, adbcmd] + cmd,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        return p.communicate()[0]

def main():
    parser = StartupOptions()
    options, args = parser.parse_args()

    # Our verify call expects options to be a real dict, so make it so
    # TODO: Generalize if we have more arguments
    opts = {"configfile": options.configfile}
    opts = parser.verify_options(opts)

    if not opts:
        print "Failed to validate options ending test"
        raise Exception('Options', 'Invalid options passed to runstartuptest')

    # Get our devicemanager instantiated - use the agent to synchronously reboot
    # and to exec processes on the device.
    dm = devicemanagerSUT.DeviceManagerSUT(opts["deviceip"], opts["deviceport"])

    # Run it
    startuptest = StartupTest(dm, opts)
    if startuptest.prepare_phone():
        startuptest.run()

if __name__ == '__main__':
    main()

