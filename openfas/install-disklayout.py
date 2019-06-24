#!/usr/bin/python
'''
Author: Hee Won Lee <knowpd@research.att.com>
Created on 1/25/2017
'''

# sudo python -c 'import os,sys; os.open("/dev/sda", os.O_EXCL)'
# lsblk | grep disk | awk '{print $4}' | sort -u
# mdadm --create --verbose /dev/md0 --level=5 --raid-devices=3 /dev/sdb1 /dev/sdc1 /dev/sdd1 --spare-devices=1 /dev/sde1

import yaml
import os, sys
import subprocess

def run_bash(cmd):
    """Run a subprocess

    Args:
        cmd (str): command

    Returns:
        result (str): stdout + stderr

    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, executable='/bin/bash')
    (stdout, stderr) = proc.communicate()
    return stdout + stderr

def get_avail_devs(blkdev_list):
    dev_avail = []
    for blkdev in blkdev_list:
        try:
            os.open('/dev/'+blkdev['name'], os.O_EXCL)
            dev_avail.append(('/dev/' + blkdev['name'],
                              blkdev['size']))
        except OSError as e:
            pass        # This means that when a device is in use, exclude it.

    return dev_avail

def get_devs_by_size(dev_avail):
    # get device sizes that are unique.
    sizes = set()
    for item in dev_avail:
        sizes.add(item[1])

    diskarray = {}
    for sz in sizes:
        dev_str = ""
        for item in dev_avail:
            if item[1] == sz:
                dev_str = dev_str + item[0] + " "
        diskarray[sz]=dev_str.rstrip()
    return diskarray



if __name__ == "__main__":
    try:
        with open('config.yaml') as stream:
            # Load config file
            config = yaml.load(stream)
            blkdev_list = config['blkdev']

            # Get available devices; i.e., exclude devices in use
            dev_avail = get_avail_devs(blkdev_list)

            # Get a dictionary: key -> size, value -> concatenation of devices
            diskarray = get_devs_by_size(dev_avail)

            # Create software raid
            redundancy = config['userreq']['redundancy']
            if redundancy == "RAID5" or redundancy == "RAID6":
                if  redundancy == "RAID5":
                    level = 5
                elif  redundancy == "RAID6":
                    level = 6
                md_idx = 0
                for key in diskarray:
                    cmd = "mdadm --create --force --verbose /dev/md" + str(md_idx)
                    cmd = cmd + " --level=" + str(level)
                    cmd = cmd + " --raid-devices=" + str(len(diskarray[key].split()))
                    cmd = cmd + " " + diskarray[key]
                    print cmd
                    # run_bash(cmd)
                    md_idx = md_idx + 1

    # For the case that config.yaml does not exist.
    except IOError as e:
        print e
