#!/usr/bin/python

import os
import sys
import yaml

# global variables.
user_config_file = 'config.yaml'


def configure_target(target):
    print "[TBD] * make some configuration changes if necessary."


def install_packages(target):
    #
    # Options: STGT|LIO|SCST|IET|SKIP
    # Cinder: http://docs.openstack.org/kilo/config-reference/content/section_volume-misc.html
    # Comparison: http://scst.sourceforge.net/comparison.html
    #
    print "* install packages for " + target

    if target == 'STGT':
        os.system('sudo apt install tgt')
    elif target == 'LIO':
        pass
    elif target == 'SCST':
        # home: http://scst.sourceforge.net/
        # ubuntu: https://launchpad.net/~ast/+archive/ubuntu/scst2
        print '! not yet supported'
        pass
    elif target == 'IET':
        os.system('sudo apt install iscsitarget')
        pass
    elif target == 'SKIP':
        print '* skipping iscsi target installation'
    else:
        print '! unknown configuration for iscsi target: [%s]' % target


def parse_input(cfgfile):
    try:
        with open(cfgfile) as stream:
            # load config file
            config = yaml.load(stream)
            target = config['userreq']['target']
            
            # install & configure
            install_packages(target)
            configure_target(target)

    except IOError as e:
        print e


if __name__ == "__main__":
    parse_input(user_config_file)
