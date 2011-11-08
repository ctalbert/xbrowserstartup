from time import sleep
import subprocess
import os
import optparse
import sys
import ConfigParser

import devicemanager,devicemanagerSUT
from devicemanager import NetworkTools

# These are configurable parameters

# The query parameters should be parameterized as %s (we fill those in later),
# and the URL MUST END with &start=
# The key of the dict is the "test type" and that will be used for reporting.
# NOTE: The 'local' string in testname is important, as functionality depends on it.
# TODO: Perhaps this could be parameterized using an external file
urls = {"local-onload": "file://%s/startup5.html?ip=%s&phone=%s&test=%s&browser=%s&start=",
        "local-twitter": "file://%s/favorites2.html?ip=%s&phone=%s&test=%s&browser=%s&start=",
        "remote-onload": "http://%s/startup5.html?ip=%s&phone=%s&test=%s&browser=%s&start=",
        "remote-twitter": "http://%s/favorites2.html?ip=%s&phone=%s&test=%s&browser=%s&start="}

# These are the components for the activity manager.  The keys are the browser
# names which are used for reporting. Note that there is only one activity 
# manager entry for both fennec-native and fennec XUL, that means that your 
# results will conflate unless we find some way to separate these two by their
# activity manager strings. 
# TODO: This should be configurable by reading in a file
apps = {"fennec": "org.mozilla.fennec/.App",
        "dolphin": "mobi.mgeek.TunnyBrowser/.BrowserActivity",
        "opera": "com.opera.browser/com.opera.Opera",
        "android": "com.android.browser/.BrowserActivity"
       }

# These are the html files that we are using for the test.  Just list file names
# here, it is assumed they are in the <htmldir> directory (see options, below)
htmlfiles = ['startup5.html', 'favorites2.html']

# This class just handles ensuring all our options are sane
class StartupOptions(optparse.OptionParser):
    def __init__(self, **kwargs):
        optparse.OptionParser.__init__(self, **kwargs)
        defaults = {}
        
        self.add_option("--configFile", action="store", type="string", dest="configfile",
                        help="Path to configuration file, if specified then no other options take effect")
        defaults["configfile"] = None
        
        self.add_option("--serverIP", action="store", type="string", dest="serverip",
                        help="IP and port of server to report results to i.e. 192.168.1.4:8080")
        defaults["serverip"] = None
        
        self.add_option("--deviceIP", action="store", type="string", dest="deviceip",
                        help="IP address of phone, assumes phone running SUTAgent")
        defaults["deviceip"] = None
        
        self.add_option("--devicePort", action="store", type="string", dest="deviceport",
                        help="Port of agent on phone, assumes 20701")
        defaults["deviceport"] = 20701
        
        self.add_option("--phone", action="store", type="string", dest="phoneid",
                        help="Text to identify the phone in test for results reporting")
        defaults["phoneid"] = None
        
        self.add_option("--phonescript", action="store", type="string", dest="script",
                        help="Shell script to run on phone, assumes this is in the local directory")
        defaults["script"] = os.path.join(os.getcwd(), "runtime.sh")
        
        self.add_option("--sdk", action="store", type="string", dest="sdk",
                        help="Full path to android sdk directory, defaults to $ANDROID_SDK if defined")
        if "ANDROID_SDK" in os.environ:
            defaults["sdk"] = os.environ["ANDROID_SDK"]
        else:
            defaults["sdk"] = None

        self.add_option("--testroot", action="store", type="string", dest="testroot",
                        help="Path on phone to area to use for testing, defaults to mnt/sdcard/startup,\
                              and will create it if it doesn't exist")
        defaults["testroot"] = "/mnt/sdcard/startup"
        
        self.add_option("--timecmd", action="store", type="string", dest="timecmd",
                        help="The path to the android binary time cmd we use, assumes it is in local directory")
        defaults["timecmd"] = os.path.join(os.getcwd(), "time")
        
        self.add_option("--local-html-dir", action="store", type="string", dest="htmldir",
                        help="Directory from which to copy the 'local' html files to the phone \
                              They will be copied to testroot/. Defaults to local directory")
        defaults["htmldir"] = os.getcwd()
        
        self.add_option("--runtype", action="store", type="string", dest="runtype",
                        help="Whether to do 'cold' or 'warm' startup test, defaults to cold")
        defaults["runtype"] = ["cold"]
        
        self.add_option("--iterations", action="store", type="int", dest="iterations",
                        help="Number of times to run a specific test/browser pair - defaults to 10")
        defaults["iterations"] = 10

        self.set_defaults(**defaults)
        
        usage = """\
python runstartuptest.py <options>
You must include the server ip address and a text notation of what phone you are
testing.  These are used for result reporting.  There are a host of other
configuration parameters that can be edited, they are at the top of the python file.

The phone you are running on MUST BE ROOTED -- we need to be able to kill the
browsers we start, without rooting, that will not work. Also, the browsers you
wish to test should be already installed, and the reporting server should already
be running."""
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

        print "DBG: options are: %s" % options
        return options
        
    def verify_options(self, options):
        if options.configfile and os.path.exists(options.configfile):
            options = self.read_config(options.configfile)
            return options

        if not options.serverip:
            print "You must specify a server IP address for result reporting"
            sys.exit(1)
        
        if not options.phoneid:
            print "You must specify a phone type for result reporting"
            sys.exit(1)
        
        if not options.sdk:
            print "You must specify the path to the android sdk"
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
            cmd = ["mkdir %s" % self.testroot]
            msg = self._run_adb("shell", cmd)
            #if msg and not "File exists" in msg:
            #    raise Exception('MakeTestRoot', 'Cannot make testroot directory')
            
            print "done with mkdir: %s" % msg
            # Copy our time script into place
            m = self._run_adb("push", [self.timecmd, "/data/local"])
            print "copy time: %s" % m
            
            # Chmod our time script - it's overkill but never trust android
            m = self._run_adb("shell", ["chmod 777 /data/local/time"])
            print "chmod: %s" % m
            
            # Copy our runscript into place
            m = self._run_adb("push", [self.script, self.testroot])
            print "copy script: %s" % m
            
            # Copy our HTML files for local use into place
            for f in self.htmlfiles:
                m = self._run_adb("push", [os.path.join(self.htmldir, f), self.testroot])
                print "copy html files: %s" % m
            
            # TODO: If we were going to configure phones with browsers on the fly
            #       then here's where you'd do it.  Right now, we're going to 
            #       punt on that.
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
                     
                    #out = self._run_adb("shell", ["sh %s %s %s" % (phonescript, app, u)], inshell=True)
                    #out = self._run_adb("shell", ["sh", phonescript, app, u])
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
    else:
        # Run it
        startuptest = StartupTest(dm, options)
        startuptest.prepare_phone()
        for i in options["runtype"]:
            startuptest.run(i)

if __name__ == '__main__':
    main()

