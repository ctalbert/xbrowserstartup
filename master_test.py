import logging
from s1s2test import S1S2Test

logging.basicConfig(filename="foo.log",
                    filemode="w",
                    level='DEBUG',
                    format='%(asctime)s|%(levelname)s|%(message)s')

p1 = S1S2Test(phoneid='galaxy-nexus',
              serial='014691061801b00b',
              ip='10.250.5.62',
              sutcmdport='20701')

jobs = [{"buildurl":'http://people.mozilla.org/~ctalbert/mobile_perf/fennec-12.0a1.en-US.android-arm.apk','blddate':'2012-01-26','revision': 'deadbeef', 'androidprocname': 'org.mozilla.fennec_ctalbert', 'version':'12','buildtype':'opt'}]

for j in jobs:
    p1.add_job(j)
p1.start_test(stop=True)
print p1.get_status()

