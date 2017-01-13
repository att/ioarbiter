#    Copyright (c) 2015 AT&T Labs Research
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#    
#    Author: Moo-Ryong Ra, mra@research.att.com

"""Tracking IOArbiter reservation"""

import ConfigParser

from oslo_log import log as logging

DEFAULT_RESV_DIR = '/var/lib/cinder/ioarb-resv/'

LOG = logging.getLogger(__name__)

# This function should be in the common library. 
# But, in order not to touch openstack distribution,
# I will keep this function locally.
def _read_info(fpath):
    """Read .ini format file."""
    config = ConfigParser.ConfigParser()
    config.read(fpath)
    return config

def get_resv_filepath(blkdev):
    return DEFAULT_RESV_DIR + 'resv-' + blkdev.split('/')[-1]

def get_resv_info(path):
    """Get current reservation information of the deployed volumes."""

    config = _read_info(path)

    data = {}
    sections = config.sections()
    for sec in sections:
        data[sec] = config.items(sec)

    return data

def add_resv_info(fpath, key, data):
    """New reservation info."""

    config = _read_info(fpath)

    if not config.has_section(key):
        config.add_section(key)

    for opt in data:
        config.set(key, opt, data[opt])

    # save it 
    with open(fpath, 'wb') as configfile:
        config.write(configfile)


def delete_resv_info(fpath, key):
    """Delete reservation info."""

    config = _read_info(fpath)

    if not config.remove_section(key):
        LOG.debug('[MRA] section does not exist: %s' % key)

    # save it to the designated location.
    with open(fpath, 'wb') as configfile:
        config.write(configfile)



