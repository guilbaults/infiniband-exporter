#!/usr/bin/env python3

import re
import time
import argparse
import subprocess
import os
import sys
import logging

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client import make_wsgi_app
from wsgiref.simple_server import make_server, WSGIRequestHandler


class InfinibandCollector(object):
    def __init__(self, can_reset_counter, input_file, node_name_map):
        self.can_reset_counter = can_reset_counter
        self.input_file = input_file
        self.node_name_map = node_name_map

        self.node_name = {}
        if self.node_name_map:
            with open(self.node_name_map) as f:
                for line in f:
                    m = re.search(r'(?P<GUID>0x.*)\s+"(?P<name>.*)"', line)
                    if m:
                        self.node_name[m.group(1)] = m.group(2)

        self.metrics = {}

        # Description based on https://community.mellanox.com/s/article/understanding-mlx5-linux-counters-and-status-parameters # noqa: E501
        # and IB specification Release 1.3
        self.counter_info = {
            'LinkDownedCounter': {
                'help': 'Total number of times the Port Training state '
                        'machine has failed the link error recovery process '
                        'and downed the link.',
                'severity': 'Error',
                'bits': 8,
            },
            'SymbolErrorCounter': {
                'help': 'Total number of minor link errors detected on one '
                        'or more physical lanes.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortXmitDiscards': {
                'help': 'Total number of outbound packets discarded by the '
                        'port because the port is down or congested',
                'severity': 'Error',
                'bits': 16,
            },
            'PortXmitWait': {
                'help': 'The number of ticks during which the port had data '
                        'to transmit but no data was sent during the entire '
                        'tick (either because of insufficient credits or '
                        'because of lack of arbitration)',
                'severity': 'Informative',
                'bits': 32,
            },
            'PortXmitData': {
                'help': 'Total number of data octets, divided by 4 (lanes), '
                        'transmitted on all VLs.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortRcvData': {
                'help': 'Total number of data octets, divided by 4 (lanes), '
                        'received on all VLs.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortXmitPkts': {
                'help': 'Total number of packets transmitted on all VLs '
                        'from this port. This may include packets with '
                        'errors.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortRcvPkts': {
                'help': 'Total number of packets received. This may include '
                        'packets containing errors',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortRcvErrors': {
                'help': 'Total number of packets containing an error that '
                        'were received on the port',
                'severity': 'Informative',
                'bits': 16,
            },
            'PortUnicastXmitPkts': {
                'help': 'Total number of unicast packets transmitted on all '
                        'VLs from the port. This may include unicast packets '
                        'with errors',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortUnicastRcvPkts': {
                'help': 'Total number of unicast packets, including unicast '
                        'packets containing errors.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortMulticastXmitPkts': {
                'help': 'Total number of multicast packets transmitted on '
                        'all VLs from the port. This may include multicast '
                        'packets with errors',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortMulticastRcvPkts': {
                'help': 'Total number of multicast packets, including '
                        'multicast packets containing errors',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortBufferOverrunErrors': {
                'help': 'Total number of packets received on the part '
                        'discarded due to buffer overrrun',
                'severity': 'Error',
                'bits': 16,
            },
            'PortLocalPhysicalErrors': {
                'help': 'Total number of packets received with physical '
                        'error like CRC error',
                'severity': 'Error',
                'bits': 16,
            },
            'PortRcvRemotePhysicalErrors': {
                'help': 'Total number of packets marked with the EBP '
                        'delimiter received on the port',
                'severity': 'Error',
                'bits': 16,
            },
            'PortInactiveDiscards': {
                'help': 'Total number of packets discarded due to the port '
                        'being in the inactive state',
                'severity': 'Error',
                'bits': 16,
            },
            'PortDLIDMappingErrors': {
                'help': 'Total number of packets on the port that could not '
                        'be forwared by the switch due to DLID mapping errors',
                'severity': 'Error',
                'bits': 16,
            },
            'LinkErrorRecoveryCounter': {
                'help': 'Total number of times the Port Training state '
                        'machine has successfully completed the link error '
                        'recovery process',
                'severity': 'Error',
                'bits': 8,
            },
            'LocalLinkIntegrityErrors': {
                'help': 'The number of times that the count of local '
                        'physical errors exceeded the threshold specified '
                        'by LocalPhyErrors',
                'severity': 'Error',
                'bits': 4,
            },
        }
        self.gauge_info = {
            'Speed': {
                'help': 'Link current speed per lane ',
            },
            'Width': {
                'help': 'Lanes per link',
            }
        }

        self.bad_status_error_metric_name = 'infiniband_bad_status_error'
        self.bad_status_error_metric_help = 'Detected bad status error \
catched on stderr of ibqueryerrors'
        self.bad_status_error_metric_labels = ['path', 'status', 'error']
        self.bad_status_error_pattern = r'src\/query\_smp\.c\:[\d]+\; (?:mad|umad) \((DR path .*) Attr .*\) bad status ([\d]+); (.*)'  # noqa: E501
        self.bad_status_error_prog = re.compile(self.bad_status_error_pattern)

    def chunks(self, x, n):
        for i in range(0, len(x), n):
            yield x[i:i + n]

    def parse_counter(self, s):
        counters = {}

        for counter in re.findall(r'\[(.*?)\]', s):
            c = re.search(r'(\w+) == (\d+).*?', counter)
            if c:
                counters[c.group(1)] = int(c.group(2))
        return counters

    def reset_counter(self, guid, port, reason):
        if guid in self.node_name:
            switch_name = self.node_name[guid]
        else:
            switch_name = guid

        if self.can_reset_counter:
            logging.info('Reseting counters on "{sw}" port {port} due to {r}'.format(  # noqa: E501
                sw=switch_name,
                port=port,
                r=reason
            ))
            process = subprocess.Popen(['perfquery', '-R', '-G', guid, port],
                                       stdout=subprocess.PIPE)
            process.communicate()
        else:
            logging.warning('Counters on "{sw}" port {port} is maxed out on {r}'.format(  # noqa: E501
                sw=switch_name,
                port=port,
                r=reason
            ))

    def build_stderr_metrics(self, stderr):
        logging.debug('Processing stderr errors retrieved by ibqueryerrors')

        bad_status_error_metric = GaugeMetricFamily(
            self.bad_status_error_metric_name,
            self.bad_status_error_metric_help,
            labels=self.bad_status_error_metric_labels)

        stderr_metrics = [bad_status_error_metric]
        error = False

        for line in stderr.splitlines():
            logging.debug('STDERR line: {}'.format(line))

            if self.process_bad_status_error(line, bad_status_error_metric):
                pass
            else:
                if not error:
                    error = True
                logging.error('Could not process line from STDERR: {}'.format(
                              line))

        return stderr_metrics, error

    def process_bad_status_error(self, line, bad_status_error_metric):
        result = self.bad_status_error_prog.match(line)
        if result:
            bad_status_error_metric.add_metric(
                [result.group(1),    # path
                 result.group(2),    # status
                 result.group(3)],   # error
                1)
            return True
        else:
            return False

    def parse_switch(self, switch_name, port, link):
        m_port = re.search(r'GUID (0x.*) port (\d+):(.*)', port)
        guid = m_port.group(1)
        port = m_port.group(2)
        counters = self.parse_counter(m_port.group(3))

        if 'Active' in link:
            if m_port.group(2) == '0':
                # Internal IB port for the SM, ignore it
                pass
            else:
                m_link = re.search(r'Link info:\s+(?P<LID>\d+)\s+(?P<port>\d+).*(?P<Width>\d)X\s+(?P<Speed>[\d+\.]*) Gbps.* Active\/  LinkUp.*(?P<remote_GUID>0x\w+)\s+(?P<remote_LID>\d+)\s+(?P<remote_port>\d+).*\"(?P<node_name>.*)\"', link)  # noqa: E501
                for gauge in self.gauge_info.keys():
                    self.metrics[gauge].add_metric([
                        switch_name,
                        guid,
                        port,
                        m_link.group('remote_GUID'),
                        m_link.group('remote_port'),
                        m_link.group('node_name')],
                        m_link.group(gauge))

                for counter in counters:
                    self.metrics[counter].add_metric([
                        switch_name,
                        guid,
                        port,
                        m_link.group('remote_GUID'),
                        m_link.group('remote_port'),
                        m_link.group('node_name')],
                        counters[counter])

                    if counters[counter] >= 2 ** (self.counter_info[counter]['bits'] - 1):  # noqa: E501
                        self.reset_counter(guid, port, counter)
        elif 'Down' in link:
            pass
        else:
            logging.error('Unknown link state on guid={} port={}'.format(
                guid, port))

    def collect(self):
        logging.debug('Start of collection cycle')
        ibqueryerrors_duration = GaugeMetricFamily(
            'infiniband_ibqueryerrors_duration_seconds',
            'Number of seconds taken to run ibqueryerrors')
        scrape_duration = GaugeMetricFamily(
            'infiniband_scrape_duration_seconds',
            'Number of seconds taken to collect and parse the stats')
        scrape_start = time.time()
        scrape_ok = GaugeMetricFamily(
            'infiniband_scrape_ok',
            'Indicate with a 1 if the scrape is valid, otherwise 0 if errors \
were encountered')

        scrape_with_errors = False

        ibqueryerrors_stdout = ""
        if self.input_file:
            with open(self.input_file) as f:
                ibqueryerrors_stdout = f.read()
        else:
            ibqueryerrors_args = [
                'ibqueryerrors',
                '--verbose',
                '--details',
                '--suppress-common',
                '--data',
                '--report-port',
                '--switch']
            if self.node_name_map:
                ibqueryerrors_args.append('--node-name-map')
                ibqueryerrors_args.append(self.node_name_map)
            if args.ca_name:
                ibqueryerrors_args.append('--Ca')
                ibqueryerrors_args.append(args.ca_name)
            ibqueryerrors_start = time.time()
            process = subprocess.Popen(ibqueryerrors_args,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            process_stdout, process_stderr = process.communicate()
            ibqueryerrors_stdout = process_stdout.decode("utf-8")

            if process_stderr:
                ibqueryerrors_stderr = process_stderr.decode("utf-8")
                logging.error(ibqueryerrors_stderr)

                stderr_metrics, error = self.build_stderr_metrics(
                    ibqueryerrors_stderr)

                for stderr_metric in stderr_metrics:
                    yield stderr_metric

                if error:
                    scrape_with_errors = True

            ibqueryerrors_duration.add_metric(
                [],
                time.time() - ibqueryerrors_start)
            yield ibqueryerrors_duration

        # need to skip the first empty line
        content = re.split(r'^Errors for (.*) \"(.*)\"',
                           ibqueryerrors_stdout,
                           flags=re.MULTILINE)[1:]

        switches = self.chunks(content, 3)

        for gauge_name in self.gauge_info:
            self.metrics[gauge_name] = GaugeMetricFamily(
                'infiniband_' + gauge_name.lower(),
                self.gauge_info[gauge_name]['help'],
                labels=[
                    'local_name',
                    'local_guid',
                    'local_port',
                    'remote_guid',
                    'remote_port',
                    'remote_name'
                ])
        for counter_name in self.counter_info:
            self.metrics[counter_name] = CounterMetricFamily(
                'infiniband_' + counter_name.lower(),
                self.counter_info[counter_name]['help'],
                labels=[
                    'local_name',
                    'local_guid',
                    'local_port',
                    'remote_guid',
                    'remote_port',
                    'remote_name'
                ])

        for sw in switches:
            switch_name = sw[1]
            for item in list(self.chunks(sw[2].split('\n'), 2))[1:-3]:
                # each item contain a list of the port and link stats
                self.parse_switch(switch_name, item[0], item[1])

        for counter_name in self.counter_info.keys():
            yield self.metrics[counter_name]
        for gauge_name in self.gauge_info.keys():
            yield self.metrics[gauge_name]

        scrape_duration.add_metric(
            [], time.time() - scrape_start)
        yield scrape_duration

        if scrape_with_errors:
            scrape_ok.add_metric([], 0)
        else:
            scrape_ok.add_metric([], 1)
        yield scrape_ok

        logging.debug('End of collection cycle')


# stolen from stackoverflow (http://stackoverflow.com/a/377028)
def which(program):
    """
    Python implementation of the which command
    """
    def is_exe(fpath):
        """ helper """
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, _ = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        paths = os.getenv("PATH", "/usr/bin:/usr/sbin:/sbin:/bin")

        for path in paths.split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None


class NoLoggingWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Prometheus collector for a infiniband fabric')
    parser.add_argument(
        '--port',
        type=int,
        default=9683,
        help='Collector http port, default is 9683')
    parser.add_argument(
        '--can-reset-counter',
        dest='can_reset_counter',
        help='Will reset counter as required when maxed out. Can also be \
set with env variable CAN_RESET_COUNTER',
        action='store_true')
    parser.add_argument(
        '--from-file',
        action='store',
        dest='input_file',
        help='Read a file containing the output of ibqueryerrors, if left \
empty, ibqueryerrors will be launched as needed by this collector')
    parser.add_argument(
        '--node-name-map',
        action='store',
        dest='node_name_map',
        help='Node name map used by ibqueryerrors. Can also be set with env \
var NODE_NAME_MAP')
    parser.add_argument(
        '--ca_name',
        type=str,
        help='ibqueryerrors ca_name for different infiniband ports')
    parser.add_argument("--verbose", help="increase output verbosity",
                        action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')

    if not which("ibqueryerrors"):
        logging.critical('Cannot find an executable ibqueryerrors binary in PATH')  # noqa: E501
        sys.exit(1)

    if args.node_name_map:
        logging.debug('Using node-name-map provided in args: {}'.format(
            args.node_name_map))
        node_name_map = args.node_name_map
    elif 'NODE_NAME_MAP' in os.environ:
        logging.debug('Using NODE_NAME_MAP provided in env vars: {}'.format(
            os.environ['NODE_NAME_MAP']))
        node_name_map = os.environ['NODE_NAME_MAP']
    else:
        logging.debug('No node-name-map was provided')
        node_name_map = None

    if args.can_reset_counter:
        logging.debug('can_reset_counter provided in args')
        can_reset_counter = True
    elif 'CAN_RESET_COUNTER' in os.environ:
        logging.debug('CAN_RESET_COUNTER provided in env vars')
        can_reset_counter = True
    else:
        logging.debug('Counters will not reset automatically')
        can_reset_counter = False

    app = make_wsgi_app(InfinibandCollector(
        can_reset_counter,
        args.input_file,
        node_name_map))
    httpd = make_server('', args.port, app,
                        handler_class=NoLoggingWSGIRequestHandler)
    httpd.serve_forever()
