Source: https://foundry.mypaytm.com/onboarding/k8s-compute

# Creating Kubernetes compute 
A `k8s` compute is not a machine. It is a pool row holding the cluster, the namespace, the pod image and the pod's resource limits, and every dispatch cuts one pod from it for one session. The pod runs `arkd` as its main container and dials out to the control plane, so nothing reaches into your cluster: no inbound route, no `kubectl port-forward`, no kubeconfig read at dispatch time. When the session ends the pod is deleted.
Because the pod is created per dispatch, a k8s pool is always cold. Setting a warm floor is rejected outright with `cannot be warm (min must be 0)`. There is nothing to keep warm.
## Before you start 
You need a cluster the control plane can reach and authenticate to, a namespace for the agent pods, and an image for them. Foundry supports three ways of naming the cluster, and the choice decides everything else about setup:
Cluster ref| What it means| Credentials  
---|---|---  
`"self"`| The conductor's own cluster| Its mounted service account  
`{ kind: "eks", ... }`| An EKS cluster named by region and cluster name| `DescribeCluster` plus an STS-minted token, optionally through a cross-account role  
`{ kind: "raw", ... }`| Any other cluster, by API server URL| Inline token or client certificate stored on the row  
Creating a pool is authorized at the tenant rung, so this is a tenant-admin action. A tenant can also be locked to specific clusters, in which case creating a pool elsewhere fails with `Tenant "<id>" is not permitted to target k8s cluster "<key>"`.
`"self"` needs no setup beyond a namespace: the conductor is already in that cluster and already authenticated. Everything below concerns the EKS case, which carries the setup cost.
Assumed throughout: an `ark` CLI signed in to your control plane ([getting access](/onboarding/getting-access)), the AWS CLI configured for the account the cluster is in, and `kubectl` pointed at that cluster. Pool creation uses the `compute` MCP tool because the CLI cannot express an EKS cluster ref; the `compute/pool/create` JSON-RPC endpoint takes the same payload.
## Onboarding an EKS cluster in your own account 
Six steps, in this order. Each one's failure hides the next, so a pool created before step 1 is done fails at provision with a timeout that says nothing about routing, and a pool created before step 5 fails later still, with a session that reads as stuck rather than misconfigured.
Steps 1, 4 and 5 need someone on the platform team, because the check or the change is on their side. Step 3 is yours, on your own cluster.
Create the namespace before you start. It depends on nothing, step 4's check runs a pod in it, and Foundry cannot create it for you later: it tries `readNamespace` and then `createNamespace` on every provision and swallows both failures, so under the namespace-scoped RBAC of step 3 neither call succeeds and pod creation returns 404 for a namespace nobody made.
bash
    
    kubectl create namespace <namespace>
    ``` Do not start until you have both the cluster name and
    the AWS account id of the **node group**, which is not always the account the
    cluster is in.
    
    | # | Step | Who does it |
    | --- | --- | --- |
    | 1 | Network path from the control plane to your API server | your DevOps, verified by the platform team |
    | 2 | Cross-account IAM role | your DevOps |
    | 3 | Kubernetes RBAC for that role | your cluster admin |
    | 4 | Network path from your pods back to the control plane | platform team |
    | 5 | ECR pull grant | you AND the platform team, one grant each |
    | 6 | The pool itself | you |
    
    Most EKS clusters worth running agents on have a private API endpoint, which is
    why step 1 exists at all: a private endpoint is not reachable from another
    account on IAM alone. `ark-sec` reports `endpointPublicAccess: false`, so every
    call the control plane makes to its API server has to arrive over a route
    inside the VPC.
    
    ### Step 1: network path from the control plane to your API server
    
    The control plane lives in account `880170353725`, CIDR `10.72.216.0/23`, and
    calls your API server on 443.
    
    Do:
    
    1. Establish a route between your VPC and `10.72.216.0/23`, by VPC peering as
       described in [team infrastructure](/onboarding/team-infra), or over a
       Transit Gateway if your account already runs one. If it is a TGW shared
       through AWS RAM, read [the RAM trap](#if-the-path-runs-over-a-shared-transit-gateway)
       below before you assume it is attached.
    2. Add an inbound rule to the **cluster security group** allowing 443 from
       `10.72.216.0/23`. Routes alone are not enough, and the failure looks like a
       hang rather than a refusal.
    
    **Done when** a probe from inside the control-plane cluster reaches your API
    server. It takes two people, because the two halves need different credentials.
    
    You, in your own account, produce the endpoint and hand it over:
    
    ```bash
    aws eks describe-cluster --region <region> --name <cluster> \
      --query cluster.endpoint --output text
The platform team then runs this in the control-plane cluster:
bash
    
    kubectl -n foundry-platform run apicheck --rm -it --restart=Never \
      --image=curlimages/curl -- \
      curl -sk -o /dev/null -w '%{http_code}\n' --max-time 5 "<endpoint>/version"
**Any HTTP status is the pass** , including `200`: Kubernetes leaves `/version` readable unauthenticated, so a number coming back at all means the connection reached the API server. `000` or a hang is the failure.
It has to run from the control-plane cluster. Your laptop probably has a VPN path the control plane does not, so a success there proves nothing about the thing being tested.
Three causes produce that hang, and only two are the ones people check:
  - No route between the VPCs.
  - The cluster security group not admitting 443 from the platform CIDR.
  - **DNS.** A private endpoint resolves through a Route 53 private hosted zone attached to your cluster's VPC. From the platform VPC the hostname resolves to nothing usable unless that zone is associated with it, or a resolver rule forwards it, and no amount of routing or security-group work fixes that. Foundry re-resolves the hostname on every dispatch, so this one bites at runtime as well as at the probe.

### Step 2: cross-account IAM role 
Generate the role from the Connect page with the EKS option ticked, per [team infrastructure](/onboarding/team-infra). With EKS on, the AWS half is read-only: `eks:DescribeCluster` on the one named cluster. Foundry resolves the endpoint and CA at dispatch and presigns a bearer token with that role, so no kubeconfig is stored anywhere and the role is the identity Kubernetes sees.
**Done when** the role exists and its trust policy names the control plane's principal and your ExternalId:
bash
    
    aws iam get-role --role-name ark-cross-account-<slug> \
      --query 'Role.AssumeRolePolicyDocument' --output json
Read it rather than assuming it: the `Principal` must be the control plane's role ARN, and the `sts:ExternalId` condition must equal the value on your tenant.
Do not try to prove this by assuming the role yourself. The generated trust policy also conditions on `sts:RoleSessionName` matching `ark-conductor*`, `ark-eks-*` or `ark-attach`, so `aws sts assume-role --role-session-name check` returns `AccessDenied` on a perfectly correct role, and your own credentials are not the trusted principal in any case. The dispatch in step 6 is what actually exercises this end to end.
### Step 3: Kubernetes RBAC for that role 
Assuming the role buys an identity the API server does not know yet, so it has to be mapped inside the cluster. How depends on the cluster's auth mode.
On an API-mode cluster, create an access entry:
bash
    
    aws eks create-access-entry --region <region> --cluster-name <cluster> \
      --principal-arn arn:aws:iam::<your-account>:role/ark-cross-account-<slug> --type STANDARD
    
    aws eks associate-access-policy --region <region> --cluster-name <cluster> \
      --principal-arn arn:aws:iam::<your-account>:role/ark-cross-account-<slug> \
      --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy \
      --access-scope type=namespace,namespaces=<namespace>
On a `CONFIG_MAP`-mode cluster those commands error out, and the mapping goes in the `aws-auth` ConfigMap instead, with a Role and RoleBinding you write.
Either way the grant is edit rights in one namespace and nothing else, which is what the provider needs: a session brings a pod, an image-pull secret, a tool-cache PVC, and the deployments and services its workspace declares. The verbs are `pods` (create, get, list, delete), `secrets` (create, replace, delete), `persistentvolumeclaims` (create, get, delete, under `toolCacheMode: shared-rwo` only), and `deployments` plus `services` (create, get, list, delete). `pods/exec` is used only by an attach-failure diagnostic, so omitting it costs a vaguer error message and nothing else.
If a managed policy is broader than your cluster's owners will accept, the access entry can instead map the role to a Kubernetes group you bind yourself. `ark-sec` is set up that way, mapped to the group `ark-provisioner` rather than to `AmazonEKSEditPolicy`.
**Done when** the policy association comes back, not merely the entry:
bash
    
    aws eks list-associated-access-policies --region <region> --cluster-name <cluster> \
      --principal-arn arn:aws:iam::<your-account>:role/ark-cross-account-<slug>
Check this one and not `describe-access-entry`. That reports the identity mapping made by the first command and says nothing about the second, so an entry whose `associate-access-policy` silently failed reads as done while granting nothing at all in the cluster.
On a `CONFIG_MAP` cluster the equivalent is finding the role ARN in `kubectl -n kube-system get cm aws-auth -o yaml`, and the binding it names in the namespace.
### Step 4: network path from your pods back to the control plane 
Every session pod runs `arkd`, which dials `controlPlaneUrl` outward and registers. This direction is easy to overlook because nothing in steps 1 to 3 exercises it, and it fails late: the pool creates fine, pods start, and the session then waits for an agent that never attached.
The URL points at the control plane's internal load balancer, so ask the platform team to add your cluster's **pod CIDR** to that load balancer's security group. Foundry's own `10.1.208.0/20` is in there for this reason.
**Done when** a pod in your cluster gets an HTTP response:
bash
    
    kubectl -n <namespace> run netcheck --rm -it --restart=Never \
      --image=curlimages/curl -- \
      curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 \
      https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/health
Any HTTP status is a pass. A timeout means the security group, not the route. Run it from a pod rather than from a node: nodes and pods can have different egress paths, and the pod is what has to reach the control plane.
### Step 5: ECR pull grant for your node account 
This step is not cluster configuration at all, which is why it gets missed. Session pods normally run `image: "self"`, which resolves to the image the control plane itself is running, in the platform account's ECR. So your kubelets pull `880170353725.dkr.ecr.<region>.amazonaws.com/pai-mlops-platform/ark:<tag>` across an account boundary, and the cross-account role from step 2 plays no part in it: that role is what the control plane assumes to talk to your API server, while this is your nodes talking to someone else's registry.
The pull needs one grant on each side:
  - **In your account** , the node role needs `ecr:GetAuthorizationToken`, an account-level action rather than a per-repository one. The managed `AmazonEC2ContainerRegistryPullOnly` or `AmazonEC2ContainerRegistryReadOnly` policy carries it; `ark-sec`'s node role has the former.
  - **In the platform account** , ask the platform team to add your node account's root to the ark repository policy for `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` and `ecr:BatchCheckLayerAvailability`. They should fetch, append and put: `set-repository-policy` replaces the whole document, so a blind write drops every other tenant's grant.

**Done when both** halves are in place. Yours, which only you can check:
bash
    
    aws iam list-attached-role-policies --role-name <your-node-role> \
      --query 'AttachedPolicies[].PolicyName'
`AmazonEC2ContainerRegistryPullOnly` or `AmazonEC2ContainerRegistryReadOnly` in that list is the pass. And theirs, which the platform team runs:
bash
    
    aws ecr get-repository-policy --repository-name pai-mlops-platform/ark \
      --region <region> --query policyText --output text | grep <your-node-account-id>
Checking only the second is the easy mistake: a cluster whose node role carries neither managed policy passes it and still lands on `Init:ImagePullBackOff`.
Without both halves the manifest HEAD returns 403, the pod goes `Init:ImagePullBackOff`, and the session parks at `ready` with nothing on the row explaining why.
If your cluster cannot pull from another account at all, pin `image` to a copy in your own registry instead of `self`, and add an `image_pull_secret` on the workspace if that registry is private. The cost is that session pods then stay on the tag you pinned rather than following fleet image bumps.
### Step 6: create the pool 
The namespace already exists, from before step 1. Create the pool as below.
**Done when** a session dispatched to the pool reaches a running stage. That is the only check that exercises all six steps at once, and Verify covers it.
### If the path runs over a shared Transit Gateway 
A TGW shared from another account through AWS RAM has three parts that look like one, and the middle is invisible in Terraform:
  1. The owner shares the TGW and creates the principal association. When the consumer account is outside the owner's AWS Organization, this lands as a **pending invitation** , not an association. Terraform reports the resource created and a re-plan shows no drift, while the consumer cannot see the gateway. Accept it explicitly:
bash
         aws ram get-resource-share-invitations --region <region>
         aws ram accept-resource-share-invitation --region <region> \
           --resource-share-invitation-arn <arn>
  2. Attach each VPC and **associate** the attachment with a TGW route table.
  3. **Propagate** the attachment into that route table, or add static routes. Association without propagation resolves nothing and reports no error.

**Done when** the gateway is visible from the consumer account, which is the check a still-pending invitation fails:
bash
    
    aws ec2 describe-transit-gateways --region <region> \
      --query 'TransitGateways[].TransitGatewayId'
One more thing about the endpoint, whichever path you used: its addresses are ENIs in your subnets and they move. On `ark-sec` they changed within an hour of being created. Foundry re-resolves the endpoint through `DescribeCluster` on every dispatch, so that costs it nothing, but any tunnel or allow-list you pinned to one of those addresses breaks silently.
## A pool in the conductor's own cluster 
This is the case the CLI wires end to end:
bash
    
    ark compute create risk-k8s \
      --kind k8s \
      --self \
      --namespace foundry-platform \
      --image ghcr.io/ytarasova/ark:latest \
      --control-plane-url https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com \
      --cpu 2 \
      --memory 8Gi
`--self` sets the cluster ref to `"self"`. `--control-plane-url` is the URL the in-pod `arkd` dials to register back; set it explicitly rather than relying on the conductor's own environment, so cluster identity lives on the row. `--service-account` attaches a pod service account for IRSA, and `--runtime-class` sets the pod's `runtimeClassName` (for example `gvisor`) when you want a stronger sandbox at the node runtime.
The cluster ref, the namespace and the image are checked for presence and shape at create time rather than at dispatch, so a target that would have put pods in the wrong place fails while you are still looking at it.
The image gets one further check, and it lands later: an untagged reference is refused at the first provision, not at create. Pin a tag or a digest, because a bare name resolves to whatever `latest` is on the day the pod is cut.
## A pool on your own EKS cluster 
The CLI has no flag for an EKS cluster ref, so create this one through the `compute` MCP tool (or the `compute/pool/create` RPC), which takes the config bag whole:
    
    compute({
      op: 'pool_create',
      name: 'risk-eks',
      compute_kind: 'k8s',
      isolation_kind: 'direct',
      config: {
        cluster: {
          kind: 'eks',
          region: 'ap-south-1',
          clusterName: 'risk-agents',
          roleArn: 'arn:aws:iam::<your-account>:role/ark-cross-account-<slug>',
          externalId: '<your external id>'
        },
        namespace: 'foundry-platform',
        image: 'self',
        controlPlaneUrl: 'https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com',
        resources: { cpu: '2', memory: '8Gi' },
        nodeSelector: { role: 'agents' },
        tolerations: [{ key: 'role', operator: 'Equal', value: 'agents', effect: 'NoSchedule' }],
        capacity: { memory: '32Gi', cpu: '800', max_sessions: 4 }
      }
    })
The `capacity` here is this pool's own arithmetic, not a number to copy: one pod is 2 cores and 8Gi, four concurrent sessions is 8 cores and 32Gi, and `capacity.cpu` counts in percent of a core, so 8 cores is `800`. Size yours the same way, from your own `resources` and the concurrency you intend. The Sizing section below covers what happens if you leave it out.
`roleArn` and `externalId` are what make it cross-account; drop both when the cluster is in the same account as the control plane. `min` may only be 0, and `max` is recorded but does not cap concurrency on a k8s pool, because there are no members to count: use `capacity` for that, as below.
`controlPlaneUrl` has to be a hostname that resolves and routes from inside your VPC. The short in-cluster form some pools use (`http://foundry-platform-control-plane:8420`) is correct only for `cluster: "self"`; a pod in your account cannot resolve a Service name in someone else's cluster.
`image: 'self'` is a sentinel meaning "whatever image the process cutting this pod is running", so session pods track fleet image bumps instead of stranding on a tag pinned in the database. It requires `ARK_SELF_IMAGE` in the environment of the process that cuts the pod, which the Helm chart injects. If that env is missing, provisioning fails with a message saying exactly that, and the fix is the chart, not a `kubectl set env` that the next sync reverts.
## Keeping pods on your own nodes 
On a shared cluster, set both halves or the placement is one-sided. A dedicated node pool is normally labelled and tainted, so `nodeSelector` alone gets the pod rejected by the taint, and a toleration alone does not stop the pod landing somewhere else:
bash
    
    ark compute update risk-eks --set nodeSelector.role=agents
Tolerations are a list, and `--set` authors objects rather than arrays, so it rejects them with ``tolerations` must be an array`. Patch those through the `compute` MCP tool, whose `config` takes JSON as it is:
    
    compute({
      op: 'update',
      name: 'risk-eks',
      config: {
        tolerations: [{ key: 'role', operator: 'Equal', value: 'agents', effect: 'NoSchedule' }]
      }
    })
The shape is checked when you set it, not when a pod is created: an `Exists` toleration carrying a `value`, or a missing key with any other operator, is refused here rather than surfacing mid-dispatch as an API-server field-path error on a session someone is waiting for.
Set these on the pool, not on a member: every per-session pod copies the pool's config, and so does every shared-service Deployment a workspace brings up. Node label values are always strings, so quote anything that looks like a number or a boolean; the create-time check rejects the unquoted form rather than letting it surface as a deserialisation error mid-dispatch.
The label also picks your ceiling. Whichever node pool it resolves to decides the instance types available, and therefore the largest pod that can ever schedule. Raising that ceiling means editing your node pool, not the Foundry one.
## Sizing 
Four numbers decide pod size and throughput, and none of them substitutes for another:
Field| What it sets  
---|---  
`config.resources`| The `arkd` container's request and limit, in Kubernetes units (`"2"` is two cores, `"500m"` half of one). Applies when the session declares no budget of its own  
workspace `resource_budget`| What actually sizes the pod on a real dispatch, so a workspace can outgrow the pool default  
`config.sidecarResources`| Charged once per declared service, plus one for DinD, in the same pod  
`config.capacity`| The admission ledger's ceiling for the whole pool row  
The pod total is `arkd` plus every sidecar, and that total has to fit on one node after daemonsets and kube-reserved take their share, which is meaningfully less than the instance spec suggests. A workspace that asks for slightly more than a node can give never schedules at all rather than scheduling slowly.
Capacity needs an explicit value, and what happens without one depends on what else the row carries. There are three outcomes, not one:
`config.capacity`| `config.resources`| What admission does  
---|---|---  
set| either| Gates against your declared ceiling  
absent| set| Derives from `resources` at 85% headroom, with `max_sessions: 1`, so the whole pool runs one session at a time  
absent| absent| Cannot size the pool at all, and refuses a budget-bearing dispatch outright  
The middle row is the one that surprises people, because the pool works and is simply serial. The last row is a hard stop rather than a slow pool, and the message names both keys. A workspace that declares no `resource_budget` is not budget-bearing and never reaches this gate at all, which is why a pool can look fine until the first workspace that does declare one lands on it.
Declare it explicitly, as per-session pod total times the concurrency you intend:
bash
    
    ark compute update risk-eks \
      --set capacity.memory=200Gi \
      --set capacity.cpu=1600 \
      --set capacity.max_sessions=8
`capacity.cpu` is a percent integer where one core is `100`, so `1600` is sixteen cores. It is the one place that does not take Kubernetes units: a value like `28000m` does not parse and the CPU half of the ceiling is silently dropped, while `resources.cpu` on the same row is Kubernetes-style and does take `500m`.
Tool caching takes one decision. By default each workspace gets a ReadWriteOnce PVC that stays warm between sessions but pins concurrent sessions for that workspace to one node. Set `toolCacheMode` to `s3` for a fleet with S3 storage configured, which keeps the cache warm with no node affinity at all, or to `per-session` for a cold cache and unbounded concurrency.
## Verify 
bash
    
    ark compute list
    ark compute show risk-eks
A pool has no power state, so `status` is empty and there is nothing to start. `show` reports its declared capacity, its current occupancy and the sessions placed on it. Then dispatch:
bash
    
    ark session start --flow bare-auto --compute risk-eks --summary "k8s smoke test"
**Done when** a pod appears in your namespace and the session moves past its first stage. The pod is named `ark-<pool>-<first 8 characters of the session id after its `s-` prefix>`, so session `s-6x89az9ygk` on pool `risk-eks` gives `ark-risk-eks-6x89az9y`. Watch for it while the session runs:
bash
    
    kubectl -n <namespace> get pods -w -l ark.dev/kind=k8s
Every session pod carries `ark.dev/kind`, plus `ark.dev/compute` holding the per-session row's name rather than the pool's, so the label above is the one that matches a pool's pods as a group. If you would rather filter client-side, `grep --line-buffered` is required: piping a watch into a bare `grep` buffers 4KB before printing anything, so the pod comes and goes while your terminal stays empty.
The pod is deleted when the session ends, so a `get pods` after the fact shows nothing and proves nothing. If no pod ever appears, the failure is before scheduling: read the session's events rather than the cluster.
This one dispatch exercises every step of the setup at once, which is why it is the only check that really closes the procedure. A pool that reaches a running stage has proved the route, the role, the RBAC, the return path and the image pull together.
## When it does not work 
Pods sitting `Pending` with `Insufficient cpu` mean the node group is full. Foundry does not scale your cluster, and a node group with no autoscaler stays that size until someone raises it, so a busy fleet stalls silently at that ceiling.
A pod `Pending` with `didn't match Pod's node affinity/selector` is a `nodeSelector` that matches no node: check the label actually on your nodes with `kubectl get nodes --show-labels`, and remember the value must be a quoted string.
`namespaces "<ns>" not found` at pod create means step 6 was skipped, or the namespace was created somewhere other than the cluster the pool points at.
A pod that never gets an IP, failing with `FailedCreatePodSandBox`, is a cluster CNI problem rather than a Foundry one. An EKS cluster built by `eksctl` without the `vpc-cni` addon shows exactly this.
A session stuck before any pod exists usually means provisioning never ran to completion: read the session's events for the failing provisioning step, which names the phase rather than reporting a generic timeout.
A dispatch refused before any pod is created, with a message naming `config.capacity` and `config.resources`, is the third row of the sizing table: the pool declares neither, so admission cannot size it. Set one of them.
A pod that runs while its session waits forever for an agent is step 4: `arkd` started and could not register back. Check the pod's logs for the dial to `controlPlaneUrl`, then check that your pod CIDR is in the control plane's load-balancer security group.
Prepare steps that work on an EC2 box and fail on a stock pod are usually about the shell rather than the pod. Hooks and actions run under a login shell, whose profile resets `PATH` and discards the composed tool paths, so a workspace that relies on them should call its tools by absolute path through `$WORKSPACE_TOOLS`.
## Next steps 
  - [Compute and isolation](/guide/compute): why `k8s` accepts only `direct` isolation.
  - [Creating EC2 compute](/onboarding/compute): the other managed kind.
  - [Workspaces](/guide/workspaces): the repos, tools and services a pod has to materialise.
  - [Team infrastructure](/onboarding/team-infra): the AWS grants, including the EKS access entry.
