#!/bin/bash
spectool -g -R infiniband-exporter-el7.spec
rpmbuild --define "dist .el7" -ba infiniband-exporter-el7.spec
