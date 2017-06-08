#!/usr/bin/env python
import sys
import logging
from optparse import OptionParser
from delay import LatencyDelayInfluxDBExporter
from trace import TraceInfluxDBExporter
from seedlink import MySeedlinkClient
from station import StationCoordInfo
import threading
from threads import ConsumerThread, ProducerThread, shutdown_event
import signal
import ast


# default logger
logger = logging.getLogger('StationCoordInfo')
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def handler(f, s):
    shutdown_event.set()


if __name__ == '__main__':

    # Select a stream and start receiving data : use regexp
    default_streams = [('FR', '.*', '(HHZ|EHZ|ELZ)', '.*'),
                       ('ND', '.*', 'HHZ', '.*'),
                       ('CL', '.*', 'HHZ', '.*'),
                       ('FR', '.*', 'SHZ', ''),
                       ('RA', '.*', 'HNZ', '00'),
                       ('RD', '.*', 'BHZ', '.*'),
                       ('G', '.*', 'BHZ', '.*'),
                       ('XX', '.*', 'BHZ', '.*'),
                       ('(SZ|RT|IG|RG)', '.*', '.*Z', '.*')
                       ]

    # Parse cmd line
    parser = OptionParser()
    parser.add_option("--dbserver", action="store", dest="dbserver",
                      default=None, help="InfluxDB server name")
    parser.add_option("--dbport", action="store", dest="dbport",
                      default='8083', help="InfluxDB server port")
    parser.add_option("--slserver", action="store", dest="slserver",
                      default='renass-fw.u-strasbg.fr',
                      help="seedlink server name")
    parser.add_option("--slport", action="store", dest="slport",
                      default='18000', help="seedlink server port")
    parser.add_option("--fdsnserver", action="store", dest="fdsn_server",
                      default=None,
                      help="fdsn station server name")
    parser.add_option("--streams", action="store", dest="streams",
                      default=default_streams, 
                      help="streams to fetch (regexp)")
    parser.add_option("--db", action="store", dest="dbname",
                      default='RT', help="InfluxDB name to use")
    parser.add_option("--dropdb",  action="store_true",
                      dest="dropdb", default=False,
                      help="[WARNING] drop previous database !")
    parser.add_option("--keep", action="store", dest="keep",
                      metavar="NUMBER", type="int",
                      default=2, help="how many days to keep data")
    parser.add_option("--recover",  action="store_true",
                      dest="recover", default=False,
                      help="use seedlink statefile " +
                           "to save/get streams from last run")
    (options, args) = parser.parse_args()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    ###########
    # geohash #
    ###########
    # Get station coordinates and compute geohash
    # seedlink and fdsn-station do not use the same mask definition :/
    if options.fdsn_server:
        logger.info("Get station coordinates from %s" % options.fdsn_server)
        # fdsn_streams = [('FR', '*', 'HHZ', '00')]
        fdsn_streams = [('*', '*', '*', '*')]
        info_sta = StationCoordInfo(options.fdsn_server, fdsn_streams)
        station_geohash = info_sta.get_geohash()
    else:
        logger.info("No FDSN server used to get station geoash")
        station_geohash = {}

    ###################
    # influxdb thread #
    ###################
    # Note: only one influxdb thread should manage database
    # for creation, drop, data rentention

    db_management = {'drop_db': options.dropdb,
                     'retention': options.keep}

    c = ConsumerThread(name='traces',
                       dbclient=TraceInfluxDBExporter,
                       args=(options.dbserver,
                             options.dbport,
                             options.dbname,
                             'seedlink',  # user
                             'seedlink',  # pwd
                             db_management,
                             station_geohash))

    db_management = False  # thread below do not manage db
    d = ConsumerThread(name='latency-delay',
                       dbclient=LatencyDelayInfluxDBExporter,
                       args=(options.dbserver,
                             options.dbport,
                             options.dbname,
                             'seedlink',  # user
                             'seedlink',  # pwd
                             db_management,
                             station_geohash))

    ###################
    # seedlink thread #
    ###################

    # forge seedLink server url
    seedlink_url = ':'.join([options.slserver, options.slport])

    statefile = str(options.dbname) + '.statefile.txt'

    p = ProducerThread(name='seedlink-reader',
                       slclient=MySeedlinkClient,
                       args=(seedlink_url, ast.literal_eval(options.streams),
                             statefile, options.recover))

    #################
    # start threads #
    #################
    p.start()
    c.start()
    d.start()

    while True:
        threads = threading.enumerate()
        if len(threads) == 1:
            break
        for t in threads:
            if t != threading.currentThread() and t.is_alive():
                t.join(.1)

    d.join()
    c.join()
    p.join()

