#!/usr/bin/env python3

import re
import time
import argparse
import subprocess
import os
import sys
import logging

from enum import Enum
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client import make_wsgi_app
from wsgiref.simple_server import make_server, WSGIRequestHandler

VERSION = "0.0.5"

class ParsingError(Exception):
    pass

class InfinibandItem(str, Enum):
    CA = 'ca'
    SWITCH = 'switch'

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

        self.scrape_with_errors = False
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
                        'port because the port is down or congested.',
                'severity': 'Error',
                'bits': 16,
            },
            # detailed description of xmitDiscards: (Head of Queue) timeout https://community.mellanox.com/s/article/howto-prevent-infiniband-credit-loops
            'PortSwHOQLifetimeLimitDiscards': {
                'help': 'The number of packets dropped by running in a head-of-Queue timeout'
                        'often caused by congestions, possibly by credit Loops.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortXmitWait': {
                'help': 'The number of ticks during which the port had data '
                        'to transmit but no data was sent during the entire '
                        'tick (either because of insufficient credits or '
                        'because of lack of arbitration).',
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
                        'packets containing errors.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortRcvErrors': {
                'help': 'Total number of packets containing an error that '
                        'were received on the port.',
                'severity': 'Informative',
                'bits': 16,
            },
            'PortUnicastXmitPkts': {
                'help': 'Total number of unicast packets transmitted on all '
                        'VLs from the port. This may include unicast packets '
                        'with errors.',
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
                        'packets with errors.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortMulticastRcvPkts': {
                'help': 'Total number of multicast packets, including '
                        'multicast packets containing errors.',
                'severity': 'Informative',
                'bits': 64,
            },
            'PortBufferOverrunErrors': {
                'help': 'Total number of packets received on the part '
                        'discarded due to buffer overrrun.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortLocalPhysicalErrors': {
                'help': 'Total number of packets received with physical '
                        'error like CRC error.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortRcvRemotePhysicalErrors': {
                'help': 'Total number of packets marked with the EBP '
                        'delimiter received on the port.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortInactiveDiscards': {
                'help': 'Total number of packets discarded due to the port '
                        'being in the inactive state.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortDLIDMappingErrors': {
                'help': 'Total number of packets on the port that could not '
                        'be forwared by the switch due to DLID mapping errors.',
                'severity': 'Error',
                'bits': 16,
            },
            'LinkErrorRecoveryCounter': {
                'help': 'Total number of times the Port Training state '
                        'machine has successfully completed the link error '
                        'recovery process.',
                'severity': 'Error',
                'bits': 8,
            },
            'LocalLinkIntegrityErrors': {
                'help': 'The number of times that the count of local '
                        'physical errors exceeded the threshold specified '
                        'by LocalPhyErrors.',
                'severity': 'Error',
                'bits': 4,
            },
            'VL15Dropped': {
                'help': 'The number of incoming VL15 packets dropped due to resource '
                        'limitations (for example, lack of buffers) in the port.',
                'severity': 'Error',
                'bits': 16,
            },
            'PortNeighborMTUDiscards': {
                'help': 'Total outbound packets discarded by the port because '
                        'packet length exceeded the neighbor MTU.',
                'severity': 'Error',
                'bits': 16,
            }
        }
        self.gauge_info = {
            'Speed': {
                'help': 'Link current speed per lane.',
            },
            'Width': {
                'help': 'Lanes per link.',
            }
        }

        self.bad_status_error_metric_name = 'infiniband_bad_status_error'
        self.bad_status_error_metric_help = 'Bad status error catched from STDERR by ibqueryerrors.'
        self.bad_status_error_metric_labels = ['path', 'status', 'error']
        self.bad_status_error_pattern = r'src\/query\_smp\.c\:[\d]+\; (?:mad|umad) \((DR path .*) Attr .*\) bad status ([\d]+); (.*)'  # noqa: E501
        self.bad_status_error_prog = re.compile(self.bad_status_error_pattern)

        self.query_failed_error_metric_name = 'infiniband_query_failed_error'
        self.query_failed_error_metric_help = 'Failed query catched from STDERR by ibqueryerrors.'
        self.query_failed_error_metric_labels = ['counter_name', 'local_name', 'lid', 'port']
        self.query_failed_error_pattern = r'ibwarn: \[\d+\] query_and_dump: (\w+) query failed on (.*), Lid (\d+) port (\d+)'
        self.query_failed_error_prog = re.compile(self.query_failed_error_pattern)

        self.mad_rpc_recv_failed_pattern = r'ibwarn: \[\d+\] _do_madrpc: recv failed: [\w\s]+'
        self.mad_rpc_recv_failed_prog = re.compile(self.mad_rpc_recv_failed_pattern)

        self.mad_rpc_failed_error_metric_name = 'infiniband_mad_rpc_failed_error'
        self.mad_rpc_failed_error_metric_help = 'ibwarn_mad_rpc error catched from STDERR by ibqueryerrors.'
        self.mad_rpc_failed_error_metric_labels = ['portid']
        self.mad_rpc_failed_error_pattern = r'ibwarn: \[\d+\] mad_rpc: _do_madrpc failed; dport \(([\w;\s]+)\)'
        self.mad_rpc_failed_error_prog = re.compile(self.mad_rpc_failed_error_pattern)

        self.query_cap_mask_error_metric_name = 'infiniband_query_cap_mask_error'
        self.query_cap_mask_error_metric_help = 'ibwarn_query_cap_mask error catched from STDERR by ibqueryerrors.'
        self.query_cap_mask_error_metric_labels = ['counter_name', 'local_name', 'portid', 'port']
        self.query_cap_mask_error_pattern = r'ibwarn: \[\d+\] query_cap_mask: (\w+) query failed on (.*), ([\w;\s]+) port (\d+)'
        self.query_cap_mask_error_prog = re.compile(self.query_cap_mask_error_pattern)

        self.print_error_metric_name = 'infiniband_print_error'
        self.print_error_metric_help = 'ibwarn_print_error catched from STDERR by ibqueryerrors.'
        self.print_error_metric_labels = ['counter_name', 'local_name', 'portid', 'port']
        self.print_error_pattern = r'ibwarn: \[\d+\] print_errors: (\w+) query failed on (.*), ([\w;\s]+) port (\d+)'
        self.print_error_prog = re.compile(self.print_error_pattern)

        self.ibqueryerrors_header_regex_str = r'^Errors for (?:0[x][\da-f]+ )?\"(.*)\"$'

        self.switch_all_ports_pattern = re.compile(r'\s*GUID 0[x][\da-f]+ port ALL: (?:\[.*\])+')

        self.port_pattern = re.compile(r'\s*GUID (0x.*) port (\d+):(.*)')
        self.link_pattern = re.compile(r'\s*Link info:\s+(\d+)\s+(\d+)\[\s+\] ==\(')
        self.active_link_pattern = re.compile(r'\s*Link info:\s+(?P<LID>\d+)\s+(?P<port>\d+).*(?P<Width>\d)X\s+(?P<Speed>[\d+\.]*) Gbps.* Active\/  LinkUp.*(?P<remote_GUID>0x\w+)\s+(?P<remote_LID>\d+)\s+(?P<remote_port>\d+).*\"(?P<node_name>.*)\"')  # noqa: E501

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
            logging.info('Reseting counters on %s port %s due to %s',  # noqa: E501
                         switch_name,
                         port,
                         reason)
            process = subprocess.Popen(['perfquery', '-R', '-G', guid, port],
                                       stdout=subprocess.PIPE)
            process.communicate()
        else:
            logging.warning('Counters on %s port %s is maxed out on %s',  # noqa: E501
                            switch_name,
                            port,
                            reason)

    def build_stderr_metrics(self, stderr):
        logging.debug('Processing stderr errors retrieved by ibqueryerrors')

        bad_status_error_metric = GaugeMetricFamily(
            self.bad_status_error_metric_name,
            self.bad_status_error_metric_help,
            labels=self.bad_status_error_metric_labels)

        query_failed_error_metric = GaugeMetricFamily(
            self.query_failed_error_metric_name,
            self.query_failed_error_metric_help,
            labels=self.query_failed_error_metric_labels)

        mad_rpc_failed_error_metric = GaugeMetricFamily(
            self.mad_rpc_failed_error_metric_name,
            self.mad_rpc_failed_error_metric_help,
            labels=self.mad_rpc_failed_error_metric_labels)

        query_cap_mask_error_metric = GaugeMetricFamily(
            self.query_cap_mask_error_metric_name,
            self.query_cap_mask_error_metric_help,
            labels=self.query_cap_mask_error_metric_labels)

        print_error_metric = GaugeMetricFamily(
            self.print_error_metric_name,
            self.print_error_metric_help,
            labels=self.print_error_metric_labels)

        stderr_metrics = [
            bad_status_error_metric,
            query_failed_error_metric,
            mad_rpc_failed_error_metric,
            query_cap_mask_error_metric,
            print_error_metric]

        error = False

        for line in stderr.splitlines():
            logging.debug('STDERR line: %s', line)

            if self.process_bad_status_error(line, bad_status_error_metric):
                pass
            elif self.process_query_failed_error(line, query_failed_error_metric):
                pass
            elif self.mad_rpc_recv_failed_prog.match(line):
                pass
            elif self.process_mad_rpc_failed(line, mad_rpc_failed_error_metric):
                pass
            elif self.process_query_cap_mask(line, query_cap_mask_error_metric):
                pass
            elif self.process_print_errors(line, print_error_metric):
                pass
            else:
                if not error:
                    error = True
                logging.error('Could not process line from STDERR: %s', line)

        return stderr_metrics, error

    def process_bad_status_error(self, line, error):

        result = self.bad_status_error_prog.match(line)

        if result:

            labels = [
                result.group(1),    # path
                result.group(2),    # status
                result.group(3)]    # error

            error.add_metric(labels, 1)

            return True

        return False

    def process_query_failed_error(self, line, error):

        result = self.query_failed_error_prog.match(line)

        if result:

            labels = [
                result.group(1),    # counter_name
                result.group(2),    # local_name
                result.group(3),    # lid
                result.group(4)]    # port

            error.add_metric(labels, 1)

            return True

        return False

    def process_mad_rpc_failed(self, line, error):

        result = self.mad_rpc_failed_error_prog.match(line)

        if result:

            labels = [result.group(1)]    # portid

            error.add_metric(labels, 1)

            return True

        return False

    def process_query_cap_mask(self, line, error):

        result = self.query_cap_mask_error_prog.match(line)

        if result:

            labels = [
                result.group(1),    # counter_name
                result.group(2),    # local_name
                result.group(3),    # portid
                result.group(4)]    # port

            error.add_metric(labels, 1)

            return True

        return False

    def process_print_errors(self, line, error):

        result = self.print_error_prog.match(line)

        if result:

            labels = [
                result.group(1),    # counter_name
                result.group(2),    # local_name
                result.group(3),    # portid
                result.group(4)]    # port

            error.add_metric(labels, 1)

            return True

        return False

    def init_metrics(self):

        for gauge_name in self.gauge_info:
            self.metrics[gauge_name] = GaugeMetricFamily(
                'infiniband_' + gauge_name.lower(),
                self.gauge_info[gauge_name]['help'],
                labels=[
                    'component',
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
                    'component',
                    'local_name',
                    'local_guid',
                    'local_port',
                    'remote_guid',
                    'remote_port',
                    'remote_name'
                ])

    def process_item(self, component, item):
        """
        The method processes ibquery ca and switch data.

        Parameters:
            * component (InfinibandItem)
            * item (Generator[List[str]])

        Throws:
            ParsingError - Raised during parsing of input content due to inconsistencies.
            RuntimeError - Raised on wrong data type for parameter passed.
        """

        if not isinstance(component, InfinibandItem):
            raise RuntimeError('Wrong data type passed for component: {}'.format(type(component)))

        if not isinstance(item, list):
            raise RuntimeError('Wrong data type passed for item: {}'.format(type(item)))

        if len(item) != 2:
            raise ParsingError('Item data incomplete:\n{}'.format(item[0]))

        name = item[0]
        data = item[1]

        item_lines = data.lstrip().splitlines()

        if InfinibandItem.SWITCH == component:

            switch_all_ports = item_lines[0]
            match_switch_all_ports = self.switch_all_ports_pattern.fullmatch(switch_all_ports)

            if match_switch_all_ports:
                del item_lines[0]
            else:
                raise ParsingError('Could not find all port information for item:\n{}'.format(name))

        for item_pair in self.chunks(item_lines, 2):

            if len(item_pair) == 2:

                port_item, link_item = item_pair

                match_port = self.port_pattern.match(port_item)

                if match_port:

                    port = int(match_port.group(2))

                    if port > 0:

                        match_link = self.link_pattern.match(link_item)

                        if not match_link:
                            raise ParsingError('No link info line match for port:\n{}'.format(port_item))

                        m_active_link = self.active_link_pattern.match(link_item)

                        if m_active_link:
                            self.parse_item(component, name, match_port, m_active_link)

                elif port_item == '' or "##" in port_item:

                    if not (link_item == '' or "##" in link_item):
                        raise ParsingError('Inconsistent data found:\nitem_pair[0]: {}\nitem_pair[1]: {}'.
                                           format(item_pair[0], item_pair[1]))

                    continue
                else:
                    raise ParsingError('Inconsistent data found:\nitem_pair[0]: {}\nitem_pair[1]: {}'.
                                       format(item_pair[0], item_pair[1]))

            else:
                if not '##' in item_pair[0]:
                    raise ParsingError('Inconsistent data found:\n{}'.format(item_pair[0]))

    def parse_item(self, component, name, match_port, match_link):

        guid = match_port.group(1)
        port = match_port.group(2)
        counters = self.parse_counter(match_port.group(3))

        for gauge in self.gauge_info:

            label_values = [
                component.value,
                name,
                guid,
                port,
                match_link.group('remote_GUID'),
                match_link.group('remote_port'),
                match_link.group('node_name')]

            self.metrics[gauge].add_metric(label_values, match_link.group(gauge))

        for counter in counters:

            label_values = [
                component.value,
                name,
                guid,
                port,
                match_link.group('remote_GUID'),
                match_link.group('remote_port'),
                match_link.group('node_name')]

            try:
                self.metrics[counter].add_metric(label_values, counters[counter])

                if counters[counter] >= 2 ** (self.counter_info[counter]['bits'] - 1):  # noqa: E501
                    self.reset_counter(guid, port, counter)
            except KeyError:
                self.scrape_with_errors = True
                logging.error('Missing description for counter metric: %s', counter)


    def collect(self):

        logging.debug('Start of collection cycle')

        self.scrape_with_errors = False

        ibqueryerrors_duration = GaugeMetricFamily(
            'infiniband_ibqueryerrors_duration_seconds',
            'Number of seconds taken to run ibqueryerrors.')
        scrape_duration = GaugeMetricFamily(
            'infiniband_scrape_duration_seconds',
            'Number of seconds taken to collect and parse the stats.')
        scrape_start = time.time()
        scrape_ok = GaugeMetricFamily(
            'infiniband_scrape_ok',
            'Indicates with a 1 if the scrape was successful and complete, '
            'otherwise 0 on any non critical errors detected '
            'e.g. ignored lines from ibqueryerrors STDERR or parsing errors.')

        self.init_metrics()

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
                '--switch',
                '--ca']
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
                
                logging.debug("STDERR output retrieved from ibqueryerrrors:\n%s",
                    ibqueryerrors_stderr)

                stderr_metrics, error = self.build_stderr_metrics(
                    ibqueryerrors_stderr)

                for stderr_metric in stderr_metrics:
                    yield stderr_metric

                if error:
                    self.scrape_with_errors = True

            ibqueryerrors_duration.add_metric(
                [],
                time.time() - ibqueryerrors_start)
            yield ibqueryerrors_duration

        content = re.split(self.ibqueryerrors_header_regex_str,
                           ibqueryerrors_stdout,
                           flags=re.MULTILINE)
        try:

            if not content:
                raise ParsingError('Input content is empty.')

            if not isinstance(content, list):
                raise RuntimeError('Input content should be a list.')

            # Drop first line that is empty on successful regex split():
            if content[0] == '':
                del content[0]
            else:
                raise ParsingError('Inconsistent input content detected:\n{}'.format(content[0]))

            input_data_chunks = self.chunks(content, 2)

            for data_chunk in input_data_chunks:

                match_switch = self.switch_all_ports_pattern.match(data_chunk[1])

                if match_switch:
                    self.process_item(InfinibandItem.SWITCH, data_chunk)
                else:
                    self.process_item(InfinibandItem.CA, data_chunk)

            for counter_name in self.counter_info:
                yield self.metrics[counter_name]
            for gauge_name in self.gauge_info:
                yield self.metrics[gauge_name]

        except ParsingError as e:
            logging.error(e)
            self.scrape_with_errors = True

        scrape_duration.add_metric([], time.time() - scrape_start)
        yield scrape_duration

        if self.scrape_with_errors:
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
    parser.add_argument('-v',
                        '--version',
                        dest='print_version',
                        required=False,
                        action='store_true',
                        help='Print version number')

    args = parser.parse_args()

    if args.print_version:
        print(f"Version {VERSION}")
        sys.exit()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')

    if args.input_file and not os.path.isfile(args.input_file):
        logging.critical("Input file does not exist: %s", args.input_file)
        sys.exit(1)

    if args.input_file is None and not which("ibqueryerrors"):
        logging.critical('Cannot find an executable ibqueryerrors binary in PATH')  # noqa: E501
        sys.exit(1)

    if args.node_name_map:
        logging.debug('Using node-name-map provided in args: %s', args.node_name_map)
        node_name_map = args.node_name_map
    elif 'NODE_NAME_MAP' in os.environ:
        logging.debug('Using NODE_NAME_MAP provided in env vars: %s', os.environ['NODE_NAME_MAP'])
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
