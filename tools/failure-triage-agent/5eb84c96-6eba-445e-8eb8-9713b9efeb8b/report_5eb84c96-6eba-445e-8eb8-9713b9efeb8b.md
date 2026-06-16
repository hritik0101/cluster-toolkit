# Triage Report

### Executive Summary
The Ansible deployment failed during the `gcluster deploy` step because the HPC Toolkit blueprint defined an invalid network configuration. This caused Terraform to receive a `googleapi: Error 400` from the Google Cloud API, halting the creation of cluster resources.

### Root Cause
The deployment failed because the blueprint (`community/examples/hpc-slurm-ubuntu2204.yaml`) attempts to create VM instances and instance templates with two network interfaces attached to the same subnetwork. This is an invalid configuration that is rejected by the Google Cloud API.

This misconfiguration stems from the module dependency graph within the blueprint. The `slurm_controller` module declares a `use` dependency on the `network` module directly, while also depending on other modules like `slurm_login` and `debug_partition` which themselves depend on the `network` module. This causes the network configuration to be inherited multiple times, resulting in a duplicate and invalid `networkInterfaces` definition in the final Terraform plan.

While the controller VM was partially created before the failure, the rest of the cluster infrastructure (login nodes, compute nodes) was not. Consequently, the Slurm services on the controller, such as `slurmctld`, are `inactive (dead)`.

### Recommended Fix
To resolve this issue, modify the `hpc-slurm-ubuntu2204.yaml` blueprint to remove the redundant network dependency. The `slurm_controller` module will still inherit the necessary network configuration through its other module dependencies.

**File to edit:** `community/examples/hpc-slurm-ubuntu2204.yaml`

**Change:**
In the `slurm_controller` module definition, remove `- network` from the `use` block.

**Before:**
```yaml
  - id: slurm_controller
    source: community/modules/scheduler/schedmd-slurm-gcp-v6-controller
    use:
    - network
    - slurm_login
    - debug_partition
    - compute_partition
    - homefs
```

**After:**
```yaml
  - id: slurm_controller
    source: community/modules/scheduler/schedmd-slurm-gcp-v6-controller
    use:
    - slurm_login
    - debug_partition
    - compute_partition
    - homefs
```

### Evidence Logs
The following logs demonstrate the root cause of the failure.

1.  **Terraform Error during VM Instance Creation:** The initial Ansible log shows the `gcluster` command failing with a `googleapi: Error 400` because the same subnetwork was specified for two different network interfaces on the same VM.

    ```log
    STDERR:
    2026-06-15T09:07:44Z Error: Error creating instance: googleapi: Error 400: Invalid value for field 'resource.networkInterfaces[1].subnetwork': 'projects/hpc-toolkit-dev/regions/us-west4/subnetworks/it-agent-slurm-v6-5eb84c-primary-subnet'. Subnetworks must be distinct for NICs attached to a VM., invalid
    ```

2.  **Terraform Error during Instance Template Creation:** The same error occurred when attempting to create instance templates for the node sets.

    ```log
    STDERR:
    2026-06-15T09:07:44Z Error: Error creating instance template: googleapi: Error 400: Invalid value for field 'resource.properties.networkInterfaces[1].subnetwork': 'projects/hpc-toolkit-dev/regions/us-west4/subnetworks/it-agent-slurm-v6-5eb84c-primary-subnet'. Subnetworks must be distinct for NICs in the same instance., invalid
    ```

3.  **Inactive Slurm Controller Service:** As a result of the incomplete infrastructure deployment, the `slurmctld` service on the controller node is not running.

    ```log
    $ systemctl status munge slurmctld slurmdbd --no-pager

    ● slurmctld.service - Slurm controller daemon
       Loaded: loaded (/lib/systemd/system/slurmctld.service; disabled; vendor preset: enabled)
       Active: inactive (dead)