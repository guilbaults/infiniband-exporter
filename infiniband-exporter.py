import re
import time
import argparse
import subprocess
import os

from prometheus_client.core import REGISTRY, CounterMetricFamily, \
    GaugeMetricFamily
from prometheus_client import start_http_server


class InfinibandCollector(object):
    def __init__(self, can_reset_counter, input_file, node_name_map):
        if can_reset_counter:
            self.can_reset_counter = True
        elif 'CAN_RESET_COUNTER' in os.environ:
            self.can_reset_counter = True
        else:
            self.can_reset_counter = False

        self.input_file = input_file

        if node_name_map:
            self.node_name_map = node_name_map
        elif 'NODE_NAME_MAP' in os.environ:
            self.node_name_map = os.environ['NODE_NAME_MAP']

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

    def chunks(self, l, n):
        for i in range(0, len(l), n):
            yield l[i:i + n]

    def parse_counter(self, s):
        counters = {}
        # init all to zero
        for counter in self.counter_info.keys():
            counters[counter] = 0

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
            print('Reseting counters on "{sw}" port {port} due to {r}'.format(
                sw=switch_name,
                port=port,
                r=reason
            ))
            process = subprocess.Popen(['perfquery', '-R', '-G', guid, port],
                                       stdout=subprocess.PIPE)
            process.communicate()
        else:
            print('Counters on "{sw}" port {port} is maxed out on {r}'.format(
                sw=switch_name,
                port=port,
                r=reason
            ))

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
                m_link = re.search(r'Link info:\s+(?P<LID>\d+)\s+(?P<port>\d+).*(?P<Width>\d)X\s+(?P<Speed>[\d+\.]*) Gbps Active\/  LinkUp.*(?P<remote_GUID>0x\w+)\s+(?P<remote_LID>\d+)\s+(?P<remote_port>\d+).*\"(?P<node_name>.*)\"', link)  # noqa: E501
                for gauge in self.gauge_info.keys():
                    self.metrics[gauge].add_metric([
                        switch_name,
                        guid,
                        port,
                        m_link.group('remote_GUID'),
                        m_link.group('remote_port'),
                        m_link.group('node_name')],
                        m_link.group(gauge))

                for counter in self.counter_info.keys():
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
            print('Unknown link state')

    def collect(self):
        ibqueryerrors = ""
        if self.input_file:
            with open(self.input_file) as f:
                ibqueryerrors = f.read()
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
            process = subprocess.Popen(ibqueryerrors_args,
                                       stdout=subprocess.PIPE)
            ibqueryerrors = process.communicate()[0].decode("utf-8")

        # need to skip the first empty line
        content = re.split(r'^Errors for (.*) \"(.*)\"',
                           ibqueryerrors,
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
        help='Node name map used by ibqueryerrors. Ccan also be set with env \
var NODE_NAME_MAP')

    args = parser.parse_args()

    start_http_server(args.port)
    REGISTRY.register(InfinibandCollector(
        args.can_reset_counter,
        args.input_file,
        args.node_name_map))
    while True:
        time.sleep(1)
