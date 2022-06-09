#!/bin/bash
cd jerma
/home/ben/git/jermabeta/.venv/bin/python3.10 main.py -mycroft /home/ben/git/mimic1/mimic \
	-mv ap \
	-jv /home/ben/jtalk/MMDAgent_Example-1.8/Voice/mei/mei_normal.htsvoice \
	-jd /home/ben/jtalk/open_jtalk_dic_utf_8-1.11
