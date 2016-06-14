# Copyright (c) 2014 AT&T Labs Research
# All Rights Reserved.
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

from oslo_log import log as logging
from oslo_serialization import jsonutils
import six

from cinder import context
from cinder.i18n import _LW
from cinder.openstack.common.scheduler import filters
from cinder.scheduler.evaluator import evaluator

from cinder.common import ioarbparams as ioarbiter
from cinder.volume import qos_specs as qos

LOG = logging.getLogger(__name__)

class IOArbiterFilter(filters.BaseHostFilter):
    """IOArbiterFilter filters hosts based on provided block devices information.

    IOArbFilter filters based on volume host's provided 'filter function'
    and metrics.
    """

    def host_passes(self, host_state, filter_properties):
        """Determines whether a host passes ioarbiter filter."""
        stats = self._generate_stats(host_state, filter_properties)

        result = self._check_filter_function(stats)
        LOG.debug("[MRA] filtering result: %s -> %s" % 
                 (stats['host_stats']['host'], result))

        return result

    def _check_filter_function(self, stats):
        """Checks if a volume passes a host's filter function.

           Returns a tuple in the format (filter_passing, filter_invalid).
           Both values are booleans.
        """
        filter_result = False

        host_stats = stats['host_stats']
        host_caps = stats['host_caps']
        extra_specs = stats['extra_specs']
        qos_specs = stats['qos_specs']
        volume_stats = stats['volume_stats']
        volume_type = stats['volume_type']

        LOG.debug('[MRA] =================')
        LOG.debug('[MRA] host_stats: %(dt)s' % {'dt': host_stats})
        LOG.debug('[MRA] host_caps: %(dt)s' % {'dt': host_caps})
        LOG.debug('[MRA] extra_specs: %(dt)s' % {'dt': extra_specs})
        LOG.debug('[MRA] volume_stats: %(dt)s' % {'dt': volume_stats})
        LOG.debug('[MRA] volume_type: %(dt)s' % {'dt': volume_type})
        LOG.debug('[MRA] qos_specs: %(dt)s' % {'dt': qos_specs})
        LOG.debug('[MRA] =================')

        # Check that the volume types match, i.e., ioarb_sttype = "ioarbiter"
        if (extra_specs is None or 'volume_backend_name' not in extra_specs):
            LOG.warning(_LW("No 'volume_backend_name' key in extra_specs. "
                            "Skipping volume backend name check."))
        elif (extra_specs['volume_backend_name'] !=
                host_stats['volume_backend_name']):
            LOG.warning(_LW("Volume backend names do not match: '%(target)s' "
                            "vs '%(current)s' :: Skipping"),
                        {'target': extra_specs['volume_backend_name'],
                         'current': host_stats['volume_backend_name']})
            return False

        # Check either host or request does not know ioarbiter.
        if not 'ioarb_cvtype' in host_caps:
            if 'ioarb_sttype' in extra_specs:
                return False
            else:
                return True
        else:
            if qos_specs is None:
                # This might be a policy decision. 
                # currently, ioarbiter cinder-volume does not
                # handle volume creation request without qos_specs.
                return False

        # Check cinder-volume type: 'host' or 'provisioned'
        cvtype = host_caps['ioarb_cvtype']
        stspec = ioarbiter.translate_qosspec(qos_specs)

        tot_budget = {}
        deployed = {}

        if cvtype == 'host':
            # Calculate host's total capacity.
            devs = host_caps['ioarb_resource']
            LOG.debug('[MRA] host mode: available devs - %(devs)s' % 
                          {'devs': host_caps['ioarb_resource']})

            if len(host_caps['ioarb_resource']) < int(stspec['ndisk']):
                LOG.debug('[MRA] %s vs. %s' % 
                    (len(host_caps['ioarb_resource']), int(stspec['ndisk'])))
                return False
    
            # Get a total budget for a) storage capacity, b) iops budget.
            # Make sure if the deployed cinder volumes are using 
            # the same translator function.
            tot_budget = ioarbiter.calculate_total_budget(devs, stspec)
        elif cvtype == 'provisioned':
            # in terms of container, it already know its total cap.
            LOG.debug('[MRA] provisioned mode')
          
            # Check capacity.
            # This function may be redundant if CapacityFilter is already 
            # used. (It is enabled by default in Kilo.)
            if volume_stats['size'] > host_stats['free_capacity_gb']: 
                return False

            # Chek RAID conf.
            if (host_caps['ioarb_raidconf'] <> stspec['raidconf'] or
                host_caps['ioarb_ndisk'] <> str(stspec['ndisk'])):
                LOG.debug('[MRA] redundancy params do not match.')
                LOG.debug('[MRA] raid %s vs. %s' % 
                              (host_caps['ioarb_raidconf'], stspec['raidconf']))
                LOG.debug('[MRA] ndisk %s vs. %s' % 
                              (host_caps['ioarb_ndisk'], stspec['ndisk']))
                return False

            # Check qos budget.
            tot_budget[ioarbiter.RTYPE_SIZE] = {
                host_caps['ioarb_raidconf']: host_stats['total_capacity_gb']}
            tot_budget[ioarbiter.RTYPE_IOPS4K] = {
                host_caps['ioarb_raidconf']: host_caps['total_iops_4k']}

        # Get already deployed qos-aware volumes' information.
        deployed = self._calculate_deployed_capacity(cvtype, host_caps, stspec)

        # See if there is a remaining capacity both in terms of 
        # capacity & qos budget.
        filter_result = (
            self._check_budget(tot_budget, deployed, stspec, 
                               ioarbiter.RTYPE_SIZE, volume_stats['size']) and
            self._check_budget(tot_budget, deployed, stspec, 
                               ioarbiter.RTYPE_IOPS4K, stspec['miniops'])
            )

        return filter_result

    def _check_budget(self, budget, deployed, stspec, rtype, reqnum):
        """We are doing a capacity (and other) check (perhaps) again 
           since CapacityFilter cannot know what could be exact available 
           storage space after QoS requirements are applied.
        """
        if (not 'raidconf' in stspec or
            not rtype in budget or
            not rtype in deployed):
            LOG.warning('[MRA] a field is missing')
            return False

        tot = float(budget[rtype][stspec['raidconf']])
        used = float(deployed[rtype][stspec['raidconf']])

        LOG.debug('[MRA] budget chk: %s, %s, %s' % (rtype, reqnum, tot-used))
        LOG.debug('[MRA] decision: %s' % (float(reqnum) < tot - used))
        
        return (float(reqnum) < tot - used)

    def _calculate_deployed_capacity(self, cvtype, hostinfo, stspec):
        """Calculate already consumed resources.
           Currently only supports size and IOPS.
        """
        deployed = {}

        if cvtype == 'host':
            # In 'host' mode, no volumes are deployed.
            deployed[ioarbiter.RTYPE_SIZE] = {
                'jbod': 0, 'raid0': 0, 'raid1': 0, 'raid5': 0, 'raid6': 0}
            deployed[ioarbiter.RTYPE_IOPS4K] = {
                'jbod': 0, 'raid0': 0, 'raid1': 0, 'raid5': 0, 'raid6': 0}
        elif cvtype == 'provisioned':
            # In 'provisioned' mode, cinder-volume process will report info.
            deployed[ioarbiter.RTYPE_SIZE] = { 
                stspec['raidconf']: hostinfo['provisioned_capacity_gb'] }
            deployed[ioarbiter.RTYPE_IOPS4K] = { 
                stspec['raidconf']: hostinfo['provisioned_iops_4k'] }
        else:
            LOG.error('[MRA] unknown cinder-volume type: %s' % cvtype)
            raise

        return deployed

    def _generate_stats(self, host_state, filter_properties):
        """Generates statistics from host and volume data."""

        host_stats = {
            'host': host_state.host,
            'volume_backend_name': host_state.volume_backend_name,
            'vendor_name': host_state.vendor_name,
            'driver_version': host_state.driver_version,
            'storage_protocol': host_state.storage_protocol,
            'QoS_support': host_state.QoS_support,
            'total_capacity_gb': host_state.total_capacity_gb,
            'allocated_capacity_gb': host_state.allocated_capacity_gb,
            'free_capacity_gb': host_state.free_capacity_gb,
            'reserved_percentage': host_state.reserved_percentage,
            'updated': host_state.updated,
        }

        host_caps = host_state.capabilities
        volume_type = filter_properties.get('volume_type', {})
        extra_specs = volume_type.get('extra_specs', {})
        request_spec = filter_properties.get('request_spec', {})
        volume_stats = request_spec.get('volume_properties', {})

        ctxt = context.get_admin_context()
        qos_specs_id = volume_type.get('qos_specs_id')
        if qos_specs_id is not None:
            qos_specs = qos.get_qos_specs(ctxt, qos_specs_id)['specs']
        else:
            qos_specs = None

        stats = {
            'host_stats': host_stats,
            'host_caps': host_caps,
            'extra_specs': extra_specs,
            'qos_specs': qos_specs,
            'volume_stats': volume_stats,
            'volume_type': volume_type,
        }

        return stats


