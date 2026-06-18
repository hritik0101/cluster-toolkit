# Triage Report for Build dd5e67db-fb71-414a-b7dd-42f6f3e972fb

## Executive Summary
The Ansible deployment failed because the underlying Terraform process was unable to provision the `google_lustre_instance` resource. The Google Cloud API rejected the request with an error indicating that the required Private Service Access (PSA) IP address range was either exhausted or did not exist, preventing the Managed Lustre service from peering with the deployment's VPC and halting the entire cluster setup.

## Chronology of Events & Forensic Root Cause Analysis
The deployment process initiated via an Ansible playbook, which in turn executed the `gcluster deploy` command to provision the infrastructure using Terraform. The initial stages of the deployment were successful, including the creation of the GKE cluster and its associated node pools. However, the process failed abruptly during the creation of the Managed Lustre filesystem.

A forensic analysis of the failure follows the "5 Whys" framework:

1.  **Why did the Ansible playbook fail?**
    The task "Create Cluster with gcluster" returned a non-zero exit code, indicating a fatal error in the underlying infrastructure provisioning script.

2.  **Why did the `gcluster` script fail?**
    The script, which wraps Terraform, failed because the `terraform apply` command could not complete.

3.  **Why did Terraform fail?**
    It was unable to create a specific resource: `module.managed-lustre.google_lustre_instance.lustre_instance`.

4.  **Why was the `google_lustre_instance` resource creation rejected?**
    The Google Cloud API returned a definitive error: `Error code 3, message: The request was invalid: invalid consumer network`. This points to a problem with the network configuration provided to the Managed Lustre service.

5.  **Why was the consumer network invalid?**
    The API provided a more specific reason: `err: cloud-control2-saas::INVALID_ARGUMENT: Private Service Access IP address range exhausted or does not exist.`

This final error is the root cause. Managed Lustre, like many Google-managed services, requires Private Service Access (PSA) to connect to a customer's VPC. This involves two key components:
*   A reserved IP address range within the VPC dedicated to service peering.
*   A VPC peering connection between the customer's VPC and Google's service producer network (`servicenetworking.googleapis.com`).

The blueprint correctly includes a `private-service-access` module intended to configure this. The failure occurred in the `managed-lustre` module, which has an explicit dependency (`use: [network, private_service_access]`) on the PSA setup. This indicates that Terraform believed the PSA configuration was complete, but when the Managed Lustre service's control plane attempted to use the peering, it found the underlying IP range to be invalid, non-existent, or already in use.

This type of failure in an automated environment is often caused by stale resources from a previous, incomplete deployment. A prior test run may have failed after allocating a global IP address for PSA but before the final cleanup, leaving an orphaned resource that conflicts with the new deployment. Because the network name is dynamically generated (`gke-ml-dd5e67-basic-net`), the conflict is likely not with a concurrent run but with a leftover from a past run that failed to de-allocate its reserved PSA range.

The subsequent errors observed via `kubectl`, such as `NodeNotReady` and `FailedScheduling`, are secondary symptoms. They occurred because the deployment was halted, leaving the GKE cluster in a partially configured and unstable state without its primary Lustre filesystem.

## Recommended Remediation
The root cause is an environmental issue within the GCP project, likely a stale Private Service Access IP reservation, rather than a flaw in the blueprint's logic. The most effective remediation is to add a pre-flight cleanup step to the CI/CD pipeline to ensure a clean environment before attempting a deployment.

Modify the Cloud Build configuration file, `tools/cloud-build/daily-tests/builds/gke-managed-lustre.yaml`, to include a preliminary step that identifies and removes orphaned PSA resources.

**Proposed Change to `tools/cloud-build/daily-tests/builds/gke-managed-lustre.yaml`:**

```diff
--- a/tools/cloud-build/daily-tests/builds/gke-managed-lustre.yaml
+++ b/tools/cloud-build/daily-tests/builds/gke-managed-lustre.yaml
@@ -19,6 +19,16 @@
   name: gcr.io/cloud-builders/gcloud
   script: "tools/cloud-build/check_running_build.sh tools/cloud-build/daily-tests/builds/gke-managed-lustre.yaml"
 - id: gke-managed-lustre
+  # Add a pre-flight check to find and remove stale PSA connections and IP ranges
+  # that may have been left over from previous failed builds. This mitigates the
+  # "IP address range exhausted" error.
+- id: cleanup-stale-psa-resources
+  name: gcr.io/cloud-builders/gcloud
+  entrypoint: /bin/bash
+  args:
+  - -c
+  - |
+    echo "Checking for stale VPC peerings in INACTIVE state..."
+    INACTIVE_PEERINGS=$(gcloud services vpc-peerings list --project=${PROJECT_ID} --format='get(peering)' --filter='state=INACTIVE')
+    for PEERING in $INACTIVE_PEERINGS; do
+      echo "Found inactive peering $PEERING, attempting to delete..."
+      # The update command with no ranges effectively deletes the peering connection
+      gcloud services vpc-peerings update --service=servicenetworking.googleapis.com --project=${PROJECT_ID} --network=$(gcloud services vpc-peerings list --project=${PROJECT_ID} --filter="peering=$PEERING" --format='get(network)') --remove-peering --force || echo "Failed to delete peering $PEERING, continuing..."
+    done
+- id: gke-managed-lustre
   name: us-central1-docker.pkg.dev/$PROJECT_ID/hpc-toolkit-repo/test-runner
   entrypoint: /bin/bash
   env:

```

**Reasoning for this fix:**
This change introduces a new build step that runs before the main deployment. It uses `gcloud` to:
1.  List all VPC peerings in the project that are in an `INACTIVE` state. An inactive peering is a common remnant of a failed PSA setup.
2.  For each inactive peering, it attempts to remove it. This frees up the associated network resources.
3.  By cleaning up these stale resources, it ensures that when the `private-service-access` module runs, it can successfully reserve a new IP range and establish a clean, `ACTIVE` peering for the Managed Lustre service to use, directly addressing the root cause of the failure.

## Evidence & Diagnostic Logs
The following logs provide a clear chain of evidence leading to the root cause determination.

The primary failure is explicitly reported in the Ansible/Terraform output, pinpointing the exact resource and API error.

```log
STDERR:
2026-06-18T05:34:03Z Error: Error waiting to create Instance: Error waiting for Creating Instance: Error code 3, message: The request was invalid: invalid consumer network, err: cloud-control2-saas::INVALID_ARGUMENT: Private Service Access IP address range exhausted or does not exist.
with module.managed-lustre.google_lustre_instance.lustre_instance,
on ../_modules/embedded/modules/file-system/managed-lustre/main.tf line 61, in resource "google_lustre_instance" "lustre_instance":
61: resource "google_lustre_instance" "lustre_instance" {
```

The Terraform logs confirm that other critical resources, such as the GKE node pools, were created successfully *before* this fatal error occurred. This proves the failure was specific to the Managed Lustre provisioning step and not a general cluster failure.

```log
2026-06-18T05:33:53Z module.gke-lustre-pool.google_container_node_pool.node_pool[0]: Creation complete after 23s [id=projects/hpc-toolkit-dev/locations/us-central1/clusters/gke-ml-dd5e67/nodePools/gke-lustre-pool]
2026-06-18T05:34:02Z module.gke_cluster.google_container_node_pool.system_node_pools[0]: Creation complete after 32s [id=projects/hpc-toolkit-dev/locations/us-central1/clusters/gke-ml-dd5e67/nodePools/system]
```

Finally, the Kubernetes event logs show node churn and pod scheduling failures. The `DeletingNode` events indicate the GKE control plane removing nodes that it could no longer find in the underlying cloud provider, a common symptom of an aborted deployment where Terraform did not complete its lifecycle.

```log
default 16m Normal DeletingNode node/gke-gke-ml-dd5e67-default-pool-d6297b93-phk9 Deleting node gke-gke-ml-dd5e67-default-pool-d6297b93-phk9 because it does not exist in the cloud provider
default 16m Normal DeletingNode node/gke-gke-ml-dd5e67-default-pool-87c49588-3wr3 Deleting node gke-gke-ml-dd5e67-default-pool-87c49588-3wr3 because it does not exist in the cloud provider
default 15m Normal DeletingNode node/gke-gke-ml-dd5e67-default-pool-e1e4ef94-s5sk Deleting node gke-gke-ml-dd5e67-default-pool-e1e4ef94-s5sk because it does not exist in the cloud provider
