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
    def __init__(self, **kwargs):
        optparse.OptionParser.__init__(self, **kwargs)
        defaults = {}

        self.add_option("--config-file", action="store", type="string", dest="configfile",
                        help="Path to configuration file, if specified then no other options take effect")
        defaults["configfile"] = None

        self.set_defaults(**defaults)

        usage = """\
python runstartuptest.py --config-file=<configfile.ini> 

You must include a config file with the options you want to run with.  See the
config.ini that lives in the tree to see what the options are and what can be
specificied.  The phone you are running on must be ROOTED and must have the
SUTAgent installed.  It also must have any browsers you want to test installed,
including Native Fennec (if not running in daemon mode).

= Daemon Mode =
Native Fennec will be downloaded from the nightly build and will be
automatically installed on the phone. You also need to include the
SUTAgent.ini file (also in the source repo) and have that placed
in /data/data/SUTAgentAndroid/files. Be sure to modify it to have proper
IP addresses for your setup.
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

        options["runtype"] = options["runtype"].split(",")

        if not options["daemon-mode"]:
            options["daemon-mode"] = False
        else:
            options["daemon-mode"] = True

        if not options["iterations"]:
            options["iterations"] = 10
        else:
            options["iterations"] = int(options["iterations"])

        return options

    def verify_options(self, options):
        if options.configfile and os.path.exists(options.configfile):
            options = self.read_config(options.configfile)
        else:
            print "You must specify a config file. See config.ini for allowable fields"
            sys.exit(1)

        if not options["serverip"]:
            print "Config file must specify a server IP address for result reporting"
            sys.exit(1)

        if not options["phoneid"]:
            print "Config file must specify a phone type for result reporting"
            sys.exit(1)
        else:
            # Ensure no spaces in phone ID
            options["phoneid"] = options["phoneid"].replace(" ","_")

        if not options["sdk"]:
            print "Config file must specify the path to the android sdk"
            sys.exit(1)
        elif not os.path.exists(options["sdk"]):
            print "The android-sdk path specified in config file is incorrect"
            sys.exit(1)

        print "opts: %s" % options
        return options


class StartupTest:
    def __init__(self, dm, options):
        self.dm = dm
        self.adb = os.path.join(options["sdk"], "platform-tools", "adb")
        self.script = options["script"]
        self.timecmd = options["timecmd"]
        self.testroot = options["testroot"]
        self.serverip = options["serverip"]
        self.phoneid = options["phoneid"]
        self.htmldir = options["htmldir"]
        self.runtype = options["runtype"]
        self.iterations = options["iterations"]

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

    def prepare_phone(self):
        try:
            # Create our testroot
            if not self.dm.mkDirs(self.testroot):
                print "Could not create directory on phone: %s" % testroot
            else:
                print "Created %s" % self.testroot

            # Copy our time script into place
            if self.dm.pushFile(self.timecmd, "/data/local/%s" % self.timecmd):
                print "Copied time"
            else:
                print "Failed to copy time binary"

            # Chmod our time script - it's overkill but never trust android
            self.dm.launchProcess(["chmod","777", "/data/local/%s" % self.timecmd])
            print "chmod process launched"

            # Copy our runscript into place
            if self.dm.pushFile(self.script, self.testroot + "/%s" % self.script):
                print "Pushed runscript to %s" % self.testroot
            else:
                print "Failed to push runscript to %s" % self.testroot

            # Copy our HTML files for local use into place
            for f in self.htmlfiles:
                if self.dm.pushFile(os.path.join(self.htmldir, f), self.testroot + "/%s" % f):
                    print "Copied htmlfile %s to %s" % (f, self.testroot)
                else:
                    print "Failed to copy htmlfile %s to %s" % (f, self.testroot)

        except Exception as e:
            print "Failed to prepare phone due to %s" % e
            sys.exit(1)

    def run(self, runtype="cold"):
        # Assume the script has been pushed to the phone, set up the path for adb
        phonescript = self.testroot + "/" + os.path.split(self.script)[1]

        for browser, app in self.apps.iteritems():
            for testname, url in self.urls.iteritems():
                for i in range(self.iterations):
                    if 'local' in testname:
                        # Then add in testroot as the server location in URL
                        u = url % (self.testroot, self.serverip, self.phoneid, testname, browser)
                    else:
                        # Then add in the server twice (once for URL, once for param)
                        u = url % (self.serverip, self.serverip, self.phoneid, testname, browser)

                    cmd = ["sh", phonescript, app, u]
                    self.dm.launchProcess(cmd)

                    # Give the html 5s to upload results
                    sleep(5)
                    print "Time up"

                    if runtype == "cold":
                        # reboot the device between runs
                        print "Rebooting device"
                        nettools = NetworkTools()
                        self.dm.reboot(nettools.getLanIp())
                        print "ok, done with reboot"
                    else:
                        print "Killing app"
                        # Then we do a warm startup, killing the process between runs
                        # The name of the process is to the left of the / in the activity manager string
                        self.dm.killProcess(app.split("/")[0])

    # cmd must be an array!
    def _run_adb(self, adbcmd, cmd, inshell=False):
        print "run adb cmd: %s" % subprocess.list2cmdline([self.adb, adbcmd] + cmd)
        if (inshell):
            p = subprocess.Popen([self.adb, adbcmd] + cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 shell=True)
        else:
            p = subprocess.Popen([self.adb, adbcmd] + cmd,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        return p.communicate()[0]

def main():
    parser = StartupOptions()
    options, args = parser.parse_args()
    options = parser.verify_options(options)

    # Get our devicemanager instantiated - use the agent to synchronously reboot
    # and to exec processes on the device.
    dm = devicemanagerSUT.DeviceManagerSUT(options["deviceip"], options["deviceport"])

    if options["daemon-mode"]:
        # Then we go into daemon mode
        print "Daemon mode, wait for phones to come to us"
        startuptest = StartupTest(dm, options)
        
    else:
        # Run it
        startuptest = StartupTest(dm, options)
        startuptest.prepare_phone()
        for i in options["runtype"]:
            startuptest.run(i)

if __name__ == '__main__':
    main()

