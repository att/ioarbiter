# IOArbiter: QoS-aware Block Storage Management in the Cloud

<a href="https://github.com/att/ioarbiter">IOArbiter</a> aims
to provide QoS-aware block storage management in the cloud environment. 
The system intends to provide the following features:

* Dynamic creation of backend block storage: the infrastructure defers an underlying storage
implementation at volume creation time, which can significantly improve overall resource utilization.

* Per-tenant IOPS allocation: a tenant can expect minimum IOPS guarantee in a per-volume basis. 

* Improved space efficiency: inline deduplication/compression, thin-provisioning features will be collectively supported by other open source projects.

If you are interested, please follow the [installation guide](INSTALL.md).

### Changelog

2016-06-06: created. 


** Currently the repository is under construction. If you have any question about the project, please contact to mra@research.att.com **
