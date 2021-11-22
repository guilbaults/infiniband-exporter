# Infiniband-Exporter
Prometheus exporter for a Infiniband fabric. This exporter only need to be installed on one server connected to the fabric, it will collect all the ports statistics on all the switches.

Metrics are identified by type, port number, switch GUID and name. The remote connection of each port is also collected. Thus each metric represents a cable between 2 switches, or between a switch and a card in a server. 

When a node name map file is provided, it will be used by `ibqueryerrors` to put a more human friendly name on switches. 

This exporter takes 3 seconds to collect the information of 60+ IB switches, and 900+ compute nodes. The information takes about 7.5MB in ASCII format for that fabric.

[Grafana dashboard example](https://grafana.com/grafana/dashboards/13260)

## Requirements

* python3
* prometheus-client (need to be installed with pip)
* ibqueryerrors

## Usage
Metrics are exported on the chosen HTTP port, events like counter reset will be on STDOUT. 

```
usage: infiniband-exporter.py [-h] [--port PORT] [--can-reset-counter]
                              [--from-file INPUT_FILE]
                              [--node-name-map NODE_NAME_MAP]
                              [--ca_name CA_NAME] [--verbose]

Prometheus collector for a infiniband fabric

optional arguments:
  -h, --help            show this help message and exit
  --port PORT           Collector http port, default is 9683
  --can-reset-counter   Will reset counter as required when maxed out. Can
                        also be set with env variable CAN_RESET_COUNTER
  --from-file INPUT_FILE
                        Read a file containing the output of ibqueryerrors, if
                        left empty, ibqueryerrors will be launched as needed
                        by this collector
  --node-name-map NODE_NAME_MAP
                        Node name map used by ibqueryerrors. Can also be set
                        with env var NODE_NAME_MAP
  --ca_name CA_NAME     ibqueryerrors ca_name for different infiniband ports
  --verbose             increase output verbosity
```
## Daemon configuration
When using the RPM, some parameters can be set in a file so systemd will pass them to the daemon (`infiniband-exporter`).

```
cat /etc/sysconfig/infiniband-exporter.conf
NODE_NAME_MAP=/etc/node-name-map
CAN_RESET_COUNTER=TRUE
```

## Metrics

InfiniBand exporter metrics are prefixed with "infiniband_".  

### Global

| Name                             | Description                                                                |
| -------------------------------- | -------------------------------------------------------------------------- |
| scrape\_ok                       | Indicates with a 1 if the scrape was successful and complete, otherwise 0. |
| scrape\_duration\_seconds        | Number of seconds taken to collect and parse the stats.                    |
| ibqueryerrors\_duration\_seconds | Number of seconds taken to run ibqueryerrors.                              |

### Errors from STDERR by ibqueryerrors

| Name                    | Labels                                   | Description                                                         |
| ----------------------- | ---------------------------------------- | ------------------------------------------------------------------- |
| bad\_status\_error      | path, status, error                      | Bad status error catched from STDERR by ibqueryerrors.              |
| query\_failed\_error    | counter\_name, local\_name, lid, port    | Failed query catched from STDERR by ibqueryerrors.                  |
| mad\_rpc\_failed\_error | portid                                   | ibwarn\_mad\_rpc error catched from STDERR by ibqueryerrors.        |
| query\_cap\_mask\_error | counter\_name, local\_name, portid, port | bwarn\_query\_cap\_mask error catched from STDERR by ibqueryerrors. |
| print\_error            | counter\_name, local\_name, portid, port | ibwarn\_print\_error catched from STDERR by ibqueryerrors.          |

### Channel Adapter (CA) and Switches

For a better readability the counter metric names are shown here in upper camel case.  
But when exported the names are displayed in lowercase and the suffix "\_total" is appended.  

Labels list:  

* component
* local\_name
* local\_guid
* local\_port
* remote\_guid
* remote\_port
* remote\_name

#### Error Counter

| Name                          | Description                                                                                                           |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| LinkDownedCounter             | Total number of times the Port Training state machine has failed the link error recovery process and downed the link. |
| SymbolErrorCounter            | Total number of minor link errors detected on one or more physical lanes.                                             |
| PortXmitDiscards              | Total number of outbound packets discarded by the port because the port is down or congested.                         |
| PortSwHOQLifetimeLimitDiscards| Total number of outbound packets discarded because they ran into a head-of-Queue timeout.                             |
| PortBufferOverrunErrors       | Total number of packets received on the part discarded due to buffer overrrun.                                        |
| PortLocalPhysicalErrors       | Total number of packets received with physical error like CRC error.                                                  |
| PortRcvRemotePhysicalErrors   | Total number of packets marked with the EBP delimiter received on the port.                                           |
| PortInactiveDiscards          | Total number of packets discarded due to the port being in the inactive state.                                        |
| PortDLIDMappingErrors         | Total number of packets on the port that could not be forwared by the switch due to DLID mapping errors.              |
| LinkErrorRecoveryCounter      | Total number of times the Port Training state machine has successfully completed the link error recovery process.     |
| LocalLinkIntegrityErrors      | The number of times that the count of local physical errors exceeded the threshold specified by LocalPhyErrors.       |
| VL15Dropped                   | The number of incoming VL15 packets dropped due to resource limitations (for example, lack of buffers) in the port.   |
| PortNeighborMTUDiscards       | Total outbound packets discarded by the port because packet length exceeded the neighbor MTU.                         |

#### Informative Counter

| Name                  | Description                                                                                                                                                                            |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PortXmitWait          | The number of ticks during which the port had data to transmit but no data was sent during the entire tick (either because of insufficient credits or because of lack of arbitration). |
| PortXmitData          | The total number of data octets, divided by 4, (counting in double words, 32 bits), transmitted on all VLs from the port.                                                              |
| PortRcvData           | The total number of data octets, divided by 4, (counting in double words, 32 bits), received on all VLs from the port.                                                                 |
| PortXmitPkts          | Total number of packets transmitted on all VLs from this port. This may include packets with errors.                                                                                   |
| PortRcvPkts           | Total number of packets (this may include packets containing Errors                                                                                                                    |
| PortRcvErrors         | Total number of packets containing an error that were received on the port.                                                                                                            |
| PortUnicastXmitPkts   | Total number of unicast packets transmitted on all VLs from the port. This may include unicast packets with errors.                                                                    |
| PortUnicastRcvPkts    | Total number of unicast packets, including unicast packets containing errors.                                                                                                          |
| PortMulticastXmitPkts | Total number of multicast packets transmitted on all VLs from the port. This may include multicast packets with errors.                                                                |
| PortMulticastRcvPkts  | Total number of multicast packets, including multicast packets containing errors.                                                                                                      |

#### Informative Gauges

| Name  | Description                  |
| ----- | ---------------------------- |
| speed | Link current speed per lane. |
| width | Lanes per link.              |

### Example

```
# HELP infiniband_linkdownedcounter_total Total number of times the Port Training state machine has failed the link error recovery process and downed the link.
# TYPE infiniband_linkdownedcounter_total counter
infiniband_linkdownedcounter_total{component="switch",local_guid="0x506b4b03005d3101",local_name="switch1",local_port="2",remote_guid="0x506b4b0300e5e461",remote_name="node1 mlx5_0",remote_port="1"} 1.0
infiniband_linkdownedcounter_total{component="switch",local_guid="0x506b4b03005d3101",local_name="switch1",local_port="3",remote_guid="0x506b4b0300c35b61",remote_name="node2 mlx5_0",remote_port="1"} 1.0
infiniband_linkdownedcounter_total{component="ca",local_guid="0x506b4b0300e5e461",local_name="node1.mlx5_0",local_port="1",remote_guid="0x506b4b03005d3101",remote_name="SwitchX -  Mellanox Technologies",remote_port="2"} 1.0
[...]
# HELP infiniband_portrcvdata_total Total number of data octets, divided by 4 (lanes), received on all VLs.
# TYPE infiniband_portrcvdata_total counter
infiniband_portrcvdata_total{component="switch",local_guid="0x506b4b03005d3101",local_name="switch1",local_port="2",remote_guid="0x506b4b0300e5e461",remote_name="node1 mlx5_0",remote_port="1"} 5.149057134655e+012
infiniband_portrcvdata_total{component="switch",local_guid="0x506b4b03005d3101",local_name="switch1",local_port="3",remote_guid="0x506b4b0300c35b61",remote_name="node2 mlx5_0",remote_port="1"} 6.051662505593e+012
```
