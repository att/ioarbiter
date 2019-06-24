#!/usr/bin/python
'''
Author: Hee Won Lee <knowpd@research.att.com>
Created on 1/17/2017
'''

import json, sys
import yaml

import subprocess

def runBash(cmd):
    """Run a subprocess

    Args:
        cmd (str): command

    Returns:
        result (str): stdout + stderr

    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, executable='/bin/bash')
    (stdout, stderr) = proc.communicate()
    return stdout + stderr

### Unit test ###
if __name__ == '__main__':
    config = {}

    # User Input
    with open('userinput.conf') as data_file:
        data = json.load(data_file)
        config['userreq'] = data

    # Block Devices
    content = runBash("lsblk |grep disk |awk '{print $1, $2, $4}'")
    content = [line.split() for line in content.split('\n') if line.strip() != '']
    blkdev = []
    for x in content:
        dev = {}
        dev['name']= x[0]
        dev['maj:min'] = x[1]
        dev['size'] = x[2]
        blkdev.append(dev)
        config['blkdev'] = blkdev

    # Dump to yaml
    print yaml.safe_dump(config, default_flow_style=False)

    with open('config.yaml', 'w') as outfile:
        yaml.safe_dump(config, outfile, default_flow_style=False)

