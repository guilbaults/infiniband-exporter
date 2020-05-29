# Infiniband-Exporter
Prometheus exporter for a Infiniband fabric. This exporter only need to be installed on one server connected to the fabric, it will collect all the ports statistics on all the switches.

Metrics are identified by type, port number, switch GUID and name. The remote connection of each port is also collected. Thus each metric represents a cable between 2 switches, or between a switch and a card in a server. 

When a node name map file is provided, it will be used by `ibqueryerrors` to put a more human friendly name on switches. 

This exporter takes 3 seconds to collect the information of 60+ IB switches, and 900+ compute nodes. The information takes about 7.5MB in ASCII format for that fabric.

## Requirements

* Python
 * prometheus-client
* `ibqueryerrors`

## Usage
Metrics are exported on the chosen HTTP port, events like counter reset will be on STDOUT. 

```
usage: infiniband-exporter.py [-h] [--port PORT] [--can-reset-counter]
                              [--from-file INPUT_FILE]
                              [--node-name-map NODE_NAME_MAP]

Prometheus collector for a infiniband fabric

optional arguments:
  -h, --help            show this help message and exit
  --port PORT           Collector http port, default is 9683
  --can-reset-counter   Will reset counter as required when maxed out
  --from-file INPUT_FILE
                        Read a file containing the output of ibqueryerrors, if
                        left empty, ibqueryerrors will be launched as needed
                        by this collector
  --node-name-map NODE_NAME_MAP
                        Node name map used by ibqueryerrors
```
# Sample
```
# HELP infiniband_linkdownedcounter_total Total number of times the Port Training state machine has failed the link error recovery process and downed the link.
# TYPE infiniband_linkdownedcounter_total counter
infiniband_linkdownedcounter_total{local_guid="0x506b4b03005d3101",local_name="switch1",local_port="2",remote_guid="0x506b4b0300e5e461",remote_name="node1 mlx5_0",remote_port="1"} 1.0
infiniband_linkdownedcounter_total{local_guid="0x506b4b03005d3101",local_name="switch1",local_port="3",remote_guid="0x506b4b0300c35b61",remote_name="node2 mlx5_0",remote_port="1"} 1.0
[...]
# HELP infiniband_portrcvdata_total Total number of data octets, divided by 4 (lanes), received on all VLs.
# TYPE infiniband_portrcvdata_total counter
infiniband_portrcvdata_total{local_guid="0x506b4b03005d3101",local_name="switch1",local_port="2",remote_guid="0x506b4b0300e5e461",remote_name="node1 mlx5_0",remote_port="1"} 5.149057134655e+012
infiniband_portrcvdata_total{local_guid="0x506b4b03005d3101",local_name="switch1",local_port="3",remote_guid="0x506b4b0300c35b61",remote_name="node2 mlx5_0",remote_port="1"} 6.051662505593e+012
```
