import Queue
import logging
from datetime import datetime

class PhoneTest:
    """
    The initialization function. It takes and stores all the information
    related to contacting this phone.
    Params:
    phoneid = ID of phone, to be used in log messages and reporting
    serial = serial number for adb style interfaces
    ip = phone's IP address (where sutagent running if it is running)
    sutcmdport = cmd port of sutagent if it is running
    sutdataport = data port of sutagent if it is running
    TODO: Add in connection data here for programmable power so we can add a
    powercycle method to this class.
    """
    def __init__(self,
                 phoneid=None,
                 serial=None,
                 ip=None,
                 sutcmdport=None,
                 sutdataport=None):
        print "This is the initialization function"
        self._jobs = Queue.Queue()
        self._phoneid = phoneid
        self._serial = serial
        self._ip = ip
        self._sutcmdport = sutcmdport
        self._sutdataport = sutdataport
        self._logger = logging.getLogger('phonetest')
        self._status = {'timestamp': datetime.now().isoformat(),
                        'online': True, 'msg': 'Initialized'}

    """
    add job - Add a job to the list of things the phone should do
    Params:
    job dict - the job is the main unit of communication with the test.
    A job is a dict of the following information:
    * buildurl - url of bundle to download and install before running test
    * revisionID - changeset ID associated with that build
    * branch - branch of build (for reporting)
    * builddate - date of build (for reporting)

    This can be called both once the phone has started testing as well as
    prior to phone start.
    """
    def add_job(self, job=None):
        # TODO: Enforce the abstract class?
        self._logger.warning("Base class adding job %s" % job)
        if job:
            self._jobs.put_nowait(job)

    """
    start Test - starts the testing process.
    This will walk the job queue and run all the jobs. If stop is True then it
    will stop the message loop once it completes the list of jobs, otherwise
    the phone will wait for more jobs to be added to the queue.
    """
    def start_test(self, stop=False):
        # TODO: Enforce abstract class here
        print "TODO: Enforce abstract class"

    """
    get status - will return some kind of status to the daemon master.
    The child object is expected to set a status message, this function
    will ensure that the master will obtain a properly formatted message.
    The returned message will have the following form:
    YYYYMMDDTHH:MM:SS.uuu|<True/False>|<msg>
    where the timestamp is the stamp the last status message was set
    the True/False is the phone's current belief as to its online state
    and the message is the last message set in the status block
    """
    def get_status(self):
        statusline = "%s|%s|%s" % (self._status["timestamp"],
                self._status["online"], self._status["msg"])
        self._logger.debug("Getting status: %s" % statusline)
        return statusline

    """
    sets the status
    Params:
    online = boolean True of False
    msg = the message of status
    """
    def _set_status(self, online=True, msg=None):
        self._logger.debug("creating status")
        self._status["timestamp"] = datetime.now().isoformat()
        self._status["online"] = online
        self._status["msg"] = msg


