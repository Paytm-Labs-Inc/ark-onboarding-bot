Source: https://foundry.mypaytm.com/onboarding/compute

# Creating EC2 compute 
An `ec2` compute is usually a machine Foundry owns end to end: it launches the instance in your AWS account, installs `arkd` on it over SSM, stops it when it goes idle, and terminates it when you destroy the row. The same kind can also adopt a box you already run, in which case Foundry installs `arkd` and nothing else, as below. That is the difference from [registering a machine you already own](/onboarding/registering-compute), where the box dials in and Foundry never touches its lifecycle.
You create it in one of two shapes. A **dedicated box** is a single named instance your team's sessions land on, which is what most teams start with. A **pool** is a blueprint that cuts a fresh instance per session up to a ceiling you set, which is what you want once several people dispatch at once. Both are covered below; the sizing, capacity, and troubleshooting sections apply to either.
## Before you start 
Your team's AWS account needs [team infrastructure](/onboarding/team-infra) in place first: VPC peering so the box can reach the control plane, and the cross-account role Foundry assumes to call EC2 and SSM in your account. Have the role ARN and ExternalId from that step to hand, along with the region, and the subnet id if you want the box on a particular one.
Creating compute needs `compute.admin`, which tenant-admins and team-admins carry and members do not. A team-admin's boxes land on their team's rung and stay reachable by that team. Creating a **pool** is authorized at the tenant rung only, because a pool's config decides where every member instance lands, so that one is a tenant-admin action.
Assumed throughout: an `ark` CLI signed in to your control plane ([getting access](/onboarding/getting-access)), the AWS CLI configured for your account, and the subnet id you want the box in.
### What the cross-account path actually needs 
The control plane never opens a connection to the instance. It drives the box through AWS Systems Manager, which is a regional AWS API, so there is no inbound route into your VPC and the launch security group is created with no ingress rules at all. Three things follow:
  - The instance needs the SSM instance profile (`ArkEC2SsmInstanceProfile` by default, carrying `AmazonSSMManagedInstanceCore`) and a way to reach the SSM endpoints, through a NAT gateway or through VPC endpoints for `ssm`, `ssmmessages` and `ec2messages`. Without it, provisioning fails at `TargetNotConnected`.
  - The cross-account role needs `iam:PassRole` for that instance profile, or `RunInstances` is rejected when it tries to attach it. The role generated from the Connect page includes it in provision mode.
  - `arkd` on the box dials the control plane outward on 443 and every session relays over that socket, so the box's subnet does need a route to the platform VPC (`10.72.216.0/23`) and outbound 443 to it. That is the one direction peering exists for.

So EC2 needs one outbound route and no inbound path at all. A Kubernetes cluster is the harder case, because the control plane calls its API server directly rather than through an AWS API: see [Creating Kubernetes compute](/onboarding/k8s-compute) for what that costs.
## A dedicated box 
Creating the row does not launch anything. It records what to launch:
bash
    
    ark compute create risk-devbox \
      --kind ec2 \
      --aws-region ap-south-1 \
      --size m \
      --arch x64 \
      --aws-subnet-id subnet-0123456789abcdef0 \
      --role-arn arn:aws:iam::<your-account>:role/ark-cross-account-<slug> \
      --external-id ark-cross-account-<slug>
Drop `--role-arn` and `--external-id` for a box in the same AWS account as the control plane; provisioning then uses the conductor's own credentials.
`--size` picks the tier: `xs` (2 vCPU, 8 GB), `s` (4/16), `m` (8/32, the default), `l` (16/64), `xl` (32/128), `xxl` (48/192), `xxxl` (64/256). `--arch` is `x64` (default) or `arm`, and decides which instance type the tier maps to (`m6i` for x64, `m6g` for arm). Leave `--aws-security-group-id` off and Foundry creates a security group with no ingress rules at all, because SSM is the transport and nothing dials into the box.
The remaining flags: `--ami-id` pins the image (the default is the latest Ubuntu 22.04 for the resolved architecture, so pin an Amazon Linux AMI if your workspace's provisioning steps use `dnf`), `--instance-profile` names the IAM instance profile attached at launch (default `ArkEC2SsmInstanceProfile`), `--idle-minutes` sets the idle auto-stop window, `--aws-tag key=value` adds tags, and `--clone-from <compute>` images an existing provisioned box so the new one boots with its software stack already on disk. Cloning needs `ec2:CreateImage`, which the generated role carries only if someone ticked **Allow compute cloning** on the Connect page; without it the clone fails at image creation.
**Done when** the row exists, which `ark compute show risk-devbox` confirms. Its status is `stopped` at this point: the row is a record of what to launch, and nothing exists in AWS yet.
Then provision it, which is the step that spends money:
bash
    
    ark compute provision risk-devbox
That runs `RunInstances`, waits for the SSM agent to come online, waits for the cloud-init ready marker, installs `arkd`, and flips the row to `running`. It takes minutes, and the ingress in front of the control plane cuts the HTTP request at 60 seconds while the work carries on server-side, so a timeout here is not a failure. Read the row instead of re-running:
bash
    
    ark compute status risk-devbox
**Done when** `ark compute status risk-devbox` reports `running`: the instance is up, `arkd` is installed, and it has registered back.
Read the row rather than re-running, but know what the CLI already did. On any client-side error, the 60-second cut included, it calls stop-instance on the row before printing `Provision failed`, so the status you find is `stopped` and not `provisioning`. That is a parked row, not a verdict on the server-side work, which may well have carried on and finished. Check the instance in the AWS console before deciding: a box that is `running` there and `stopped` here finished provisioning after the CLI gave up on it.
A genuine failure leaves status `failed` with `last_provision_error` in the config, which outlives the request that was cut and names the cause. Re-provisioning is safe either way: when the row already points at a live instance, provision adopts it rather than launching a second one.
### Idle auto-stop 
A managed EC2 box gets `idleMinutes: 30` unless you say otherwise, so a box with no sessions on it is stopped after half an hour and the row stays. The next dispatch wakes it. Pass `--idle-minutes 0` at create (or set it later) to keep a box running permanently, and `--idle-minutes 120` to widen the window.
## A pool instead of a box 
A pool holds the launch config; members are cut from it on demand:
bash
    
    ark compute pool create risk-pool \
      --compute ec2/direct \
      --size m \
      --region ap-south-1 \
      --max 4 \
      --idle-ttl 1800
`ark compute pool create` only carries size, region and image, so set the rest of the launch config on the pool afterwards. Pools are always editable, unlike a running box:
bash
    
    ark compute update risk-pool \
      --set subnetId=subnet-0123456789abcdef0 \
      --set securityGroupId=sg-0123456789abcdef0 \
      --set roleArn=arn:aws:iam::<your-account>:role/ark-cross-account-<slug> \
      --set externalId=ark-cross-account-<slug>
Dispatch picks a member in a fixed order: the running member with the most free capacity (ties broken least-recently-used), else a stopped member which the dispatch path wakes, else a fresh member launched from the pool config, up to `--max`. At the ceiling with everything saturated, dispatch fails with `at max capacity`, so `--max` is a real cost limit and not a hint. One member hosts one session unless you raise `capacity.max_sessions` on the pool.
Point sessions at the pool by name, the same way you would a box: `--compute risk-pool`. A tenant-admin can also make it the fallback for dispatches that name no compute at all, with `ark compute pool set-default risk-pool`.
**Done when** a session dispatched to the pool lands on a member. A pool itself has no power state, so `ark compute pool list` showing it is not proof of anything: dispatch once, then `ark compute list` and look for a row named `risk-pool-<hex>`. That is the member the pool cut, and its status tells you whether the launch config on the pool is right: if the config is wrong, the failure appears on the member and not on the pool. Member is the word the CLI and the pool code use, and it is worth keeping distinct from the two other senses of "clone" on this page, which are the AMI `--clone-from` makes and the per-session row a k8s pool cuts.
## Adopting an instance you already run 
First, tag the instance. In adopt mode the generated role's SSM reach and its one mutation (`StartInstances`) are both conditioned on the ownership tag, and the policy forbids Ark from applying that tag to anything it did not create. So this is yours to run, once per instance, and skipping it fails provisioning with `AccessDenied` rather than anything mentioning a tag:
bash
    
    aws ec2 create-tags --resources i-0123456789abcdef0 --tags Key=ark:managed,Value=true
Tagging grants reachability only. The role's `Deny` statements mean it does not make the box terminable or stoppable through that role.
Then create the row with `--instance-id`, which records the existing box instead of launching one. Provision describes that instance, wakes it if it is stopped, and installs `arkd` on it over SSM without ever calling `RunInstances`. `--aws-region` is required here, unlike for a launch, for the reason given below:
bash
    
    ark compute create risk-buildbox \
      --kind ec2 \
      --instance-id i-0123456789abcdef0 \
      --aws-region ap-south-1 \
      --role-arn arn:aws:iam::<your-account>:role/ark-cross-account-<slug> \
      --external-id ark-cross-account-<slug>
The row records that you asked to adopt, not just the id, and three behaviours follow from that marker.
Foundry will not launch a replacement for your box. If describing the instance answers that it is gone, which is what a wrong region, a wrong account or a typo that still matches `i-` plus hex all produce, provisioning fails and names the region and account it looked in. Without the marker that same answer reads as a dead instance and provisions a fresh one, which is why `--instance-id` requires an explicit `--aws-region`: the region otherwise resolves to `$AWS_REGION` or `us-east-1`, and looking in the wrong place is the common way to be told your instance does not exist.
Foundry will not terminate it either. `ark compute destroy` refuses on an adopted row and tells you to remove the compute row and terminate the instance yourself if that is what you want.
It gets no automatic idle stop. A box Foundry launches is stopped after 30 idle minutes; an adopted one is left alone, because that policy is not Foundry's to apply to your machine. Pass `--idle-minutes 30` if you do want it.
One thing the marker does not cover: `ark compute stop` still stops the box on demand, because the capability that gates it belongs to the compute kind rather than the row. A cross-account role generated in **adopt-only** mode denies stop and terminate outright at IAM, which is the protection that holds regardless.
If you want Foundry to have no say in the lifecycle at all, enrol the machine as a `registered-host` instead: `arkd` runs there and dials out, and Foundry provisions and terminates nothing. See [registering a machine you already own](/onboarding/registering-compute).
## Taking a box away 
`ark compute stop risk-devbox` parks it and keeps the row, which is what you want between bursts of work. `ark compute destroy risk-devbox` terminates the instance and removes the row, and on a box Foundry launched that is exactly what it does, with no undo. Sessions running on the box at the time are stranded rather than drained, so check `ark compute show risk-devbox` for an empty session roster first.
An adopted box refuses destroy, per adopting an instance you already run.
## Capacity and admission 
Admission control decides how much work a box will accept before it queues the rest, and it is on by default. When a compute declares no `capacity`, Foundry derives a conservative one from the instance size at 85% headroom, reserving the rest for the OS, `arkd`, and the agent runtime. An `m` box (8 vCPU, 32 GB) therefore admits against roughly 27Gi and 680% CPU.
An adopted box carries no size, because Foundry did not choose its instance type and will not invent one, so declaring capacity is not optional there: until you do, a workspace that declares a `resource_budget` is refused on it with a message naming exactly this. A workspace declaring no budget is not gated at all, so an uncapped adopted box will accept work regardless.
Declare it explicitly whenever you want a different ceiling, and always on an adopted box:
bash
    
    ark compute update risk-devbox \
      --set capacity.memory=24Gi \
      --set capacity.cpu=600 \
      --set capacity.max_sessions=4
`capacity.cpu` is a percent integer where one core is `100`, so `600` is six cores. A Kubernetes-style value like `500m` does not parse and is ignored, which silently removes the CPU half of the ceiling. `capacity.memory` is a systemd-style size (`24Gi`, `200M`). Both are read fresh on every admission and dispatch decision, so they can be edited on a running box without stopping it; most other config keys are baked in at provision time and the row refuses the edit until you stop it.
## Verify 
bash
    
    ark compute list
    ark compute show risk-devbox
    ark compute metrics risk-devbox
`show` reports the box's declared capacity, its current occupancy, and the sessions placed on it. `list` reports each target's architecture once the box has checked in, which is how you confirm an arm box came up as arm64.
None of that proves dispatch works. Finish by running a small session pinned to the target:
bash
    
    ark session start --flow bare-auto --compute risk-devbox --summary "compute smoke test"
`bare-auto` is a single-agent flow that needs no workspace, so it isolates the compute: if its stage starts running, the box was reachable and `arkd` launched an agent on it. Repeat with `--workspace <name>` to prove the box can also clone that workspace's repos, which is the other half of the placement rule.
## When it does not work 
A provision that hangs and then fails with `TargetNotConnected` means SSM never saw the instance: the instance profile is missing or carries no `AmazonSSMManagedInstanceCore`, or the subnet has no route to the SSM endpoints.
`AccessDenied` on an SSM call against an **adopted** box is a different fault with the same shape: the role's SSM grant is conditioned on the ownership tag, so the instance is missing `ark:managed=true`.
A provision that refuses with `will not launch a replacement` means the instance was not found where Foundry looked. Check `--aws-region` and whether the box lives in the account your `--role-arn` reaches, rather than the control plane's own.
A `RunInstances` rejection about passing a role means the cross-account role lacks `iam:PassRole` for the instance profile the launch attaches. The role generated from the Connect page includes it in provision mode; a hand-written policy usually does not.
`Tenant "<id>" is not permitted to attach IAM instance profile "<name>"` means your tenant has an allow-list and this profile is not on it. Leave `--instance-profile` off to take the default SSM profile.
A session that queues forever on a box with free room is usually the capacity ceiling rather than the box: check `ark compute show` for occupancy against capacity, and check that `capacity.cpu` parses as a percent integer.
A dispatch refused with the compute's architecture in the message means the workspace declares an `arch` the box does not have. `ark compute list` reports what the box actually is once it has checked in, which is often the first time anyone notices an `--arch arm` row on an x86 instance type or the reverse.
A row that stays `stopped` and never wakes on dispatch usually has no `instance_id`, meaning it was created but never provisioned. `ark compute show` shows it: run `ark compute provision` rather than waiting.
`compute '<name>' already exists` on create means the name is taken in this tenant, including by a row someone else made. Names are unique per tenant, not per person.
## Next steps 
  - [Compute and isolation](/guide/compute): the kinds, the legal pairs, and how placement works.
  - [Creating Kubernetes compute](/onboarding/k8s-compute): pools that cut a pod per session.
  - [Registering a machine you already own](/onboarding/registering-compute): the operator-owned path.
  - [Workspaces](/guide/workspaces): the repos and tools the box has to be able to reach.
