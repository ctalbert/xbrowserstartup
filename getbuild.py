import os
import ConfigParser
from pulsebuildmonitor import start_pulse_monitor

def on_build(msg):
    # Use the msg to get the build and install it then kick off our tests
    print "---------- BUILD FOUND ----------"
    print "%s" % msg
    print "---------------------------------"

    # We will get a msg on busted builds with no URLs, so just ignore
    # those, and only run the ones with real URLs
    if "buildurl" in msg:
        url = msg["buildurl"]
        cfg = ConfigParser.RawConfigParser()
        cfg.read("builds.ini")
        if not cfg.has_section("builds"):
            cfg.add_section("builds")
        cfg.set("builds", "url", url)
        cfg.set("builds", "installed", 0)
        cfg.write(open("builds.ini", "wb"))

# TODO: This could be a useful tool if it took some arguments...
def main():
    if not os.path.exists("builds.ini"):
        open("builds.ini", "wb")
    # Start our pulse listener for the birch builds
    pulsemonitor = start_pulse_monitor(buildCallback=on_build,
                                            tree=["birch"],
                                            platform=["linux-android"],
                                            mobile=False,
                                            buildtype="opt"
                                            )
    pulsemonitor.join()

if __name__ == "__main__":
    main()