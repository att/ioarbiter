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

"""IOArbiter QoS parameter mapping

   These information will be applied both for 
   cinder-volume and cinder-scheduler.
"""

from oslo_log import log as logging

# IOArbiter specific keys.
STTYPE = 'ioarb_sttype'
STTYPE_MANUAL = 'ioarb-manual'

RTYPE_SIZE = 'size'
RTYPE_IOPS4K = 'iops-4k'
RTYPE_IOPS4K_R = 'iops-4k-r'
RTYPE_IOPS4K_W = 'iops-4k-w'

# Constants for software RAID configuration. ('storage_class' field)
RAID_MAPPING = {
    'ioarb-platinum': 'raid0',
    'ioarb-gold': 'raid6',
    'ioarb-silver': 'raid5',
    'ioarb-bronze': 'jbod' }
NDISK_MAPPING = {
    'ioarb-platinum': 3,
    'ioarb-gold': 5,
    'ioarb-silver': 4,
    'ioarb-bronze': 1 }
MAX_IOPS_MAPPING = {
    'ioarb-platinum': 30000,
    'ioarb-gold': 1000,
    'ioarb-silver': 500,
    'ioarb-bronze': 100 }
MIN_IOPS_MAPPING = {
    'ioarb-platinum': 30000,
    'ioarb-gold': 1000,
    'ioarb-silver': 500,
    'ioarb-bronze': 100 }
IO_SIZE_MAPPING = {
    'ioarb-platinum': 4096,
    'ioarb-gold': 4096,
    'ioarb-silver': 4096,
    'ioarb-bronze': 4096 }
MEDIUM_MAPPING = {
    'ioarb-platinum': 'ssd',
    'ioarb-gold': 'any',
    'ioarb-silver': 'any',
    'ioarb-bronze': 'any' }

LOG = logging.getLogger(__name__)

def translate_qosspec(qosspec):
    """ Requirement mapping """
    stspec = {}
    sttype = qosspec[STTYPE]
    if sttype in RAID_MAPPING:
        stspec = {
            'raidconf': RAID_MAPPING[sttype],
            'maxiops': MAX_IOPS_MAPPING[sttype],
            'miniops': MIN_IOPS_MAPPING[sttype],
            'iosize': IO_SIZE_MAPPING[sttype],
            'medium': MEDIUM_MAPPING[sttype],
            'ndisk': NDISK_MAPPING[sttype] }
    elif sttype == STTYPE_MANUAL:
        # u'max_iops': u'100', u'medium': u'hdd', u'blocksize': u'4096', 
        # u'raidconf': u'jbod', u'min_iops': u'100'
        stspec = {
            'raidconf': qosspec['raidconf'],
            'maxiops': qosspec['maxiops'],
            'miniops': qosspec['miniops'],
            'iosize': qosspec['iosize'],
            'medium': qosspec['medium'],
            'ndisk': qosspec['ndisk'] }
    else:
        LOG.debug('[MRA] unknown sttype: %s' % (sttype))
        stspec = {
            'raidconf': 'jbod',
            'maxiops': 0,
            'miniops': 0,
            'iosize': 4096,
            'medium': 'hdd',
            'ndisk': 1 }

    return stspec

def get_perf_dict(ndisk, medium, iotype):
    """Basic IOPS budgetting"""

    unit = 200
    if medium == 'hdd':
        unit = 200
    elif medium == 'ssd':
        unit = 70000
    elif medium == 'nvme':
        unit = 700000

    r = {
        'jbod': unit,
        'raid0': ndisk * unit,
        'raid1': ndisk * unit,
        'raid5': ndisk * unit, 
        'raid6': ndisk * unit, } 
    w = { 
        'jbod': unit, 
        'raid0': ndisk * unit, 
        'raid1': unit,
        'raid5': (ndisk-1) * unit, 
        'raid6': (ndisk-2) * unit, }
    rw = {
        'jbod': min(r['jbod'], w['jbod']),
        'raid0': min(r['raid0'], w['raid0']),
        'raid1': min(r['raid1'], w['raid1']),
        'raid5': min(r['raid5'], w['raid5']),
        'raid6': min(r['raid6'], w['raid6']), }

    if iotype == 'r':
        return r
    elif iotype == 'w':
        return w
    else:
        return rw

def calculate_total_budget(devs, stspec):
    """Calculate total budget.
       devs: ioarb_resource from the ioarblvm driver impl.
       stspec: from translate_qosspec() above.
    """
    budget = {}

    # Capacity calculation. (in GB)
    mindisk = float(min(dev['size'] for dev in devs))
    totdisk = float(sum(dev['size'] for dev in devs))
    ndisk = len(devs)

    budget[RTYPE_SIZE] = {
        'jbod': totdisk,
        'raid0': totdisk,
        'raid1': totdisk / 2.0,
        'raid5': mindisk * (ndisk - 1) if ndisk > 2 else 0,    # minimum=3
        'raid6': mindisk * (ndisk - 2) if ndisk > 3 else 0,    # minimum=4
    }

    # IOPS budget calculation. (based on 4KB randrw)
    # - calculation is based on the following link. 
    #     - https://en.wikipedia.org/wiki/Standard_RAID_levels
    # - might be replaced with profiled data.
    budget[RTYPE_IOPS4K_R] = get_perf_dict(ndisk, stspec['medium'], 'r')
    budget[RTYPE_IOPS4K_W] = get_perf_dict(ndisk, stspec['medium'], 'w')
    budget[RTYPE_IOPS4K] = get_perf_dict(ndisk, stspec['medium'], 'rw')

    return budget




