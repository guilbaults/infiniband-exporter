#!/bin/bash
spectool -g -R infiniband-exporter-el9.spec
rpmbuild --define "dist .el9" -ba infiniband-exporter-el9.spec
