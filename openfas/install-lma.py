#!/usr/bin/python
'''
Author: Hee Won Lee <knowpd@research.att.com>
Created on 2/15/2017
'''
import yaml
import os, sys
import subprocess

# global variables.
user_config_file = 'config.yaml'

def run_bash(cmd):
    """Run a subprocess

    Args:
        cmd (str): command

    Returns:
        result (str): stdout + stderr

    """
    print cmd
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, executable='/bin/bash')
    (stdout, stderr) = proc.communicate()
    return stdout + stderr

def install_diamond():
    run_bash("pip install diamond")
    run_bash("mkdir -p /etc/diamond")
    run_bash("mkdir -p /var/log/diamond")
    run_bash("cp diamond/diamond.conf /etc/diamond/")
    run_bash("cp -r diamond/collectors /etc/diamond/")
    run_bash("diamond")

def install_telegraf():
    run_bash("wget https://dl.influxdata.com/telegraf/releases/telegraf_1.2.1_amd64.deb")
    run_bash("dpkg -i telegraf_1.2.1_amd64.deb")

def parse_input(cfgfile):
    try:
        with open(cfgfile) as stream:
            # load config file
            config = yaml.load(stream)
            monitor = config['userreq']['monitor']

            # install
            if monitor == 'DIAMOND':
                install_diamond()
            elif monitor == 'TELEGRAF':
                install_telegraf()

    except IOError as e:
        print e


if __name__ == "__main__":
    parse_input(user_config_file)

