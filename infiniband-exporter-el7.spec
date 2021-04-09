Name:	  infiniband-exporter
Version:  0.0.3
%global gittag 0.0.3
Release:  1%{?dist}
Summary:  Prometheus exporter for a Infiniband Fabric

License:  Apache License 2.0
URL:      https://github.com/guilbaults/infiniband-exporter
Source0:  https://github.com/guilbaults/%{name}/archive/v%{gittag}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:	systemd
Requires:       python2-prometheus_client
Requires:	infiniband-diags

%description
Prometheus exporter for a Infiniband fabric. This exporter only need to be installed on one server connected to the fabric, it will collect all the ports statistics on all the switches.

Metrics are identified by type, port number, switch GUID and name. The remote connection of each port is also collected. Thus each metric represents a cable between 2 switches, or between a switch and a card in a server.

When a node name map file is provided, it will be used by ibquerryerror to put a more human friendly name on switches.

This exporter takes 3 seconds to collect the information of 60+ IB switches, and 900+ compute nodes. The information takes about 7.5MB in ASCII format for that fabric.

%prep
%autosetup -n %{name}-%{gittag}
%setup -q

%build

%install
mkdir -p %{buildroot}/%{_bindir}
mkdir -p %{buildroot}/%{_unitdir}

sed -i -e 's$#!/usr/bin/env python3$#!/usr/bin/python2$g' infiniband-exporter.py
install -m 0755 %{name}.py %{buildroot}/%{_bindir}/%{name}
install -m 0644 infiniband-exporter.service %{buildroot}/%{_unitdir}/infiniband-exporter.service

%clean
rm -rf $RPM_BUILD_ROOT

%files
%{_bindir}/%{name}
%{_unitdir}/infiniband-exporter.service

%changelog
* Fri Apr 09 2021 Simon Guilbault <simon.guilbault@calculquebec.ca> 0.0.3-1
- Adding ca_name option
- Adding a real logging output instead of print()
- Adding scrape duration and status
- Detect when ibqueryerrors is not executable
* Mon Mar 30 2020 Simon Guilbault <simon.guilbault@calculquebec.ca> 0.0.2-1
- Fixing counter reset using python subprocess.Popen()
* Fri Mar 20 2020 Simon Guilbault <simon.guilbault@calculquebec.ca> 0.0.1-1
- Initial release
